"""script.md・スライド画像・VOICEVOX音声から、字幕付きの完成動画を組み立てる。

ffmpegを外部コマンドとして呼び出す(要インストール。Macなら `brew install ffmpeg`)。
話者ごとに字幕の色を変える(つむぎ=黄色系、ずんだもん=緑系)ため、SRTではなく
ASS(Advanced SubStation Alpha)形式の字幕を生成し、ffmpegの`ass`フィルタで
焼き込む。日本語フォントはfontconfig経由の解決に頼らず、同梱の静的Noto Sans JP
(Bold/Regular)を`fontsdir`オプションで直接指定する
(可変フォントだとfontconfigがウェイトを正しく解決できず、文字化けする事例があったため)。

流れ:
  1. script_parser.parse_script()でscript.mdをシーン・セリフに分解
     (voicevox_agentのLLM抽出ではなく、正規表現による決定的パースを使う。
     どのセリフがどのシーン=どのスライドに対応するかを確実にするため)
  2. 各セリフをVOICEVOX ENGINEで直接音声合成し、長さを計測
  3. slides/manifest.jsonのscene_numberから、各スライドの表示時間
     (=対応するシーンの音声の合計時間)を計算
  4. セリフごとのタイミングでASS字幕(話者別に色分け)を生成
  5. ffmpegで (a)音声を結合 (b)スライド画像を表示時間通りに並べた無音動画を作成
     (c) 動画+音声+字幕を1本のmp4に合成
"""

import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.script_parser import parse_script
from video_pipeline.voicevox_client import (
    DEFAULT_BASE_URL,
    DEFAULT_STYLE_NAME,
    list_speakers,
    resolve_speaker_id,
    synthesize,
)

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

# 話者ごとの字幕色(ASS形式 &HAABBGGRR)。黄色系/緑系。
SUBTITLE_STYLE_COLORS = {
    "つむぎ": "&H0000E5FF",  # 黄色系 (R255,G229,B0)
    "ずんだもん": "&H0055AA55",  # 緑系 (R85,G170,B85)
}
DEFAULT_STYLE_COLOR = "&H00FFFFFF"  # 白(未知の話者向けフォールバック)

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 25
TITLE_SLIDE_DURATION_SECONDS = 2.0
# 対応するスライドが見つからないシーンの最小表示時間(秒)。極端に短い
# 無表示区間を避けるための下限。
MIN_SLIDE_DURATION_SECONDS = 0.5


@dataclass
class TimedLine:
    speaker: str
    text: str
    scene_number: int
    start: float
    end: float
    audio_path: Path


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate)


def _run_ffmpeg(args: list[str]) -> None:
    command = ["ffmpeg", "-y", *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpegの実行に失敗しました。ffmpegがインストールされているか確認してください。\n"
            f"コマンド: {' '.join(command)}\n"
            f"エラー出力:\n{result.stderr[-4000:]}"
        )


def _format_ass_time(seconds: float) -> str:
    """ASSのタイムスタンプ形式(H:MM:SS.cc)に変換する。"""
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _quote_ffmpeg_filter_value(value: str) -> str:
    """ffmpegのフィルタグラフ構文でパス等を安全に渡すためシングルクォートで囲む。

    ffmpegのフィルタ引数パーサーは":"を区切り文字として使うため、パスに
    ":"が含まれる場合や、ffmpegのバージョンによって位置引数(filename=を
    省略した書き方)を受け付けない場合に備え、常に明示的なkey=value形式
    かつシングルクォート囲みで渡す(呼び出し側でfilename=/fontsdir=を付ける)。
    """
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"


def synthesize_timeline(
    script_text: str,
    work_dir: str | Path,
    style_map: dict[str, str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[TimedLine]:
    """script.mdの全セリフを音声合成し、開始/終了時刻つきのタイムラインを作る。

    script_parserで決定的にパースしたセリフを、登場順にそのままVOICEVOXへ
    渡す(voicevox_script.txtは経由しない)。これにより字幕テキスト・音声・
    シーン番号が常に一致することを保証する。
    """
    style_map = style_map or {}
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    scenes = parse_script(script_text)
    if not scenes:
        raise ValueError(
            "台本からシーン・セリフを1つも抽出できませんでした。"
            "script.mdが「### シーン<N>：」見出しと「つむぎ「〜」」形式の"
            "セリフを含んでいるか確認してください。"
        )

    speakers = list_speakers(base_url)
    speaker_id_cache: dict[str, int] = {}

    timeline: list[TimedLine] = []
    cursor = TITLE_SLIDE_DURATION_SECONDS

    for scene in scenes:
        for line in scene.lines:
            if line.speaker not in speaker_id_cache:
                style_name = style_map.get(line.speaker, DEFAULT_STYLE_NAME)
                speaker_id_cache[line.speaker] = resolve_speaker_id(
                    speakers, line.speaker, style_name
                )
            speaker_id = speaker_id_cache[line.speaker]

            index = len(timeline) + 1
            print(f"  {index:03d}: [シーン{line.scene_number}/{line.speaker}] {line.text[:30]}...")
            wav_bytes = synthesize(line.text, speaker_id, base_url)
            audio_path = work_dir / f"line_{index:04d}.wav"
            audio_path.write_bytes(wav_bytes)

            duration = _wav_duration_seconds(audio_path)
            timeline.append(
                TimedLine(
                    speaker=line.speaker,
                    text=line.text,
                    scene_number=line.scene_number,
                    start=cursor,
                    end=cursor + duration,
                    audio_path=audio_path,
                )
            )
            cursor += duration

    return timeline


def _build_ass_subtitle(timeline: list[TimedLine], output_path: str | Path) -> Path:
    """話者ごとに色分けしたASS字幕ファイルを生成する。"""
    style_lines = []
    for speaker, color in SUBTITLE_STYLE_COLORS.items():
        style_lines.append(
            f"Style: {speaker},Noto Sans JP,64,{color},&H000000FF,&H00000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,3,2,2,80,80,60,1"
        )
    style_lines.append(
        f"Style: Default,Noto Sans JP,64,{DEFAULT_STYLE_COLOR},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,2,2,80,80,60,1"
    )

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {VIDEO_WIDTH}\n"
        f"PlayResY: {VIDEO_HEIGHT}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "\n".join(style_lines)
        + "\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for item in timeline:
        style_name = item.speaker if item.speaker in SUBTITLE_STYLE_COLORS else "Default"
        start = _format_ass_time(item.start)
        end = _format_ass_time(item.end)
        text = item.text.replace("\n", "\\N").replace("{", "").replace("}", "")
        events.append(f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{text}")

    output_path = Path(output_path)
    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output_path


def _load_slides_manifest(slides_dir: Path) -> list[dict]:
    manifest_path = slides_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} が見つかりません。video-pipelineを再実行して"
            "scene_number付きのスライドを生成し直してください。"
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _build_visual_timeline(
    timeline: list[TimedLine], slides_dir: Path
) -> list[tuple[Path, float]]:
    """(スライド画像パス, 表示秒数) のリストを、シーンごとの音声時間から作る。"""
    manifest = _load_slides_manifest(slides_dir)

    title_entry = next((m for m in manifest if m["scene_number"] is None), None)
    scene_to_files: dict[int, list[str]] = {}
    for entry in manifest:
        scene_number = entry.get("scene_number")
        if scene_number is None:
            continue
        scene_to_files.setdefault(scene_number, []).append(entry["file"])

    scene_duration: dict[int, float] = {}
    for item in timeline:
        scene_duration[item.scene_number] = (
            scene_duration.get(item.scene_number, 0.0) + (item.end - item.start)
        )

    visual_timeline: list[tuple[Path, float]] = []
    if title_entry:
        visual_timeline.append((slides_dir / title_entry["file"], TITLE_SLIDE_DURATION_SECONDS))

    for scene_number in sorted(scene_duration):
        total = scene_duration[scene_number]
        files = scene_to_files.get(scene_number)
        if not files:
            print(
                f"  [警告] シーン{scene_number}に対応するスライドが見つかりません。"
                "直前のスライドの表示を延長します。"
            )
            if visual_timeline:
                prev_path, prev_duration = visual_timeline[-1]
                visual_timeline[-1] = (prev_path, prev_duration + total)
            continue

        per_slide = max(total / len(files), MIN_SLIDE_DURATION_SECONDS)
        for file_name in files:
            visual_timeline.append((slides_dir / file_name, per_slide))

    return visual_timeline


def _write_concat_file(entries: list[tuple[str, float | None]], output_path: Path) -> Path:
    """ffmpeg concat demuxer用のリストファイルを書き出す。

    entries: [(ファイルパス, 表示秒数 or None), ...]
    最後の要素はconcat demuxerの既知の挙動(最後のdurationが無視される)を
    避けるため、durationなしで同じファイルをもう一度書き足す。
    """
    lines = []
    for path, duration in entries:
        lines.append(f"file '{path}'")
        if duration is not None:
            lines.append(f"duration {duration:.6f}")
    if entries:
        lines.append(f"file '{entries[-1][0]}'")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def assemble_video(
    script_path: str | Path,
    slides_dir: str | Path,
    output_path: str | Path,
    work_dir: str | Path | None = None,
    style_map: dict[str, str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> Path:
    """script.md + スライド画像 + VOICEVOX音声から、色分け字幕つきのmp4を組み立てる。"""
    script_path = Path(script_path)
    slides_dir = Path(slides_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(work_dir) if work_dir else output_path.parent / "_video_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    script_text = script_path.read_text(encoding="utf-8")

    print("=== 音声合成中 ===")
    timeline = synthesize_timeline(script_text, work_dir / "audio", style_map, base_url)

    print("=== 字幕(ASS)を生成中 ===")
    ass_path = _build_ass_subtitle(timeline, work_dir / "captions.ass")

    print("=== スライドの表示時間を計算中 ===")
    visual_timeline = _build_visual_timeline(timeline, slides_dir)

    print("=== 音声を結合中 ===")
    audio_concat_path = _write_concat_file(
        [(str(item.audio_path.resolve()), None) for item in timeline],
        work_dir / "audio_concat.txt",
    )
    full_audio_path = work_dir / "full_audio.wav"
    _run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(audio_concat_path), "-c", "copy", str(full_audio_path)]
    )

    print("=== スライド映像を生成中 ===")
    image_concat_path = _write_concat_file(
        [(str(path.resolve()), duration) for path, duration in visual_timeline],
        work_dir / "images_concat.txt",
    )
    silent_video_path = work_dir / "silent_video.mp4"
    _run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(image_concat_path),
            "-vf", f"fps={VIDEO_FPS},format=yuv420p",
            "-r", str(VIDEO_FPS),
            str(silent_video_path),
        ]
    )

    print("=== 音声・映像・字幕を合成中 ===")
    ass_path_arg = _quote_ffmpeg_filter_value(str(ass_path.resolve()))
    fonts_dir_arg = _quote_ffmpeg_filter_value(str(FONTS_DIR.resolve()))
    _run_ffmpeg(
        [
            "-i", str(silent_video_path),
            "-i", str(full_audio_path),
            "-vf", f"ass=filename={ass_path_arg}:fontsdir={fonts_dir_arg}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(output_path),
        ]
    )

    print(f"\n完了: {output_path}")
    return output_path
