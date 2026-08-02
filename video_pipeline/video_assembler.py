"""script.md・スライド画像・VOICEVOX音声から、字幕付きの完成動画を組み立てる。

ffmpegを外部コマンドとして呼び出す(要インストール。Macなら `brew install ffmpeg-full`)。
話者ごとに字幕の色を変える(つむぎ=黄色系、ずんだもん=緑系)ため、SRTではなく
ASS(Advanced SubStation Alpha)形式の字幕を生成し、ffmpegの`ass`フィルタで
焼き込む。日本語フォントはfontconfig経由の解決に頼らず、同梱の静的Noto Sans JP
(Bold/Regular)を`fontsdir`オプションで直接指定する
(可変フォントだとfontconfigがウェイトを正しく解決できず、文字化けする事例があったため)。

流れ:
  1. script_parser.parse_script()でscript.mdをシーン・セリフに分解
     (voicevox_agentのLLM抽出ではなく、正規表現による決定的パースを使う。
     どのセリフがどのシーン=どのスライドに対応するかを確実にするため)
  2. 各セリフをVOICEVOX ENGINEで直接音声合成し、長さを計測。セリフ間には
     自然な会話に見えるよう無音の"間"(PAUSE_BETWEEN_LINES_SECONDS)を挿入する
     (話者+セリフ内容でキャッシュするため、同じ台本での再実行時は再合成しない)
  3. slides/manifest.jsonのscene_numberから、各スライドの表示時間を計算
     (シーンの開始〜次のシーンの開始までの実時間を使い、セリフ間の"間"も
     取りこぼさないようにする)
  4. セリフごとのタイミングでASS字幕(話者別に色分け)を生成
  5. (任意) assets/characters/に立ち絵の口開閉2状態(closed/open)のPNGが
     揃っていれば、そのキャラクターが喋っている区間だけ口を開いた画像に
     切り替えるオーバーレイをffmpegのoverlay+enableで合成する
     (画像はcharacter_renderer.pyでPSD立ち絵素材から書き出す)
  6. ffmpegで (a)音声を結合 (b)スライド画像を表示時間通りに並べた無音動画を作成
     (c) 動画+音声+字幕(+キャラクター)を1本のmp4に合成

タイミングの一致について(重要):
  音声トラック・字幕・スライド表示時間は、すべて同じ「cursor」の積み上げ
  (無音のタイトル区間 + 各セリフの実測時間 + セリフ間の間)から計算しており、
  この3つが食い違わないようにしている。以前は音声トラック側にタイトル区間の
  無音を入れ忘れていたため、字幕・映像より音声が2秒ほど早く進んでしまう
  不具合があった。
"""

import hashlib
import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_pipeline.script_parser import flatten_lines, parse_script
from video_pipeline.voicevox_client import (
    DEFAULT_BASE_URL,
    DEFAULT_STYLE_NAME,
    list_speakers,
    resolve_speaker_id,
    synthesize,
)

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
CHARACTER_ASSETS_DIR = Path(__file__).parent / "assets" / "characters"

# 話者ごとの字幕色(ASS形式 &HAABBGGRR)。黄色系/緑系。
SUBTITLE_STYLE_COLORS = {
    "つむぎ": "&H0000E5FF",  # 黄色系 (R255,G229,B0)
    "ずんだもん": "&H0055AA55",  # 緑系 (R85,G170,B85)
}
DEFAULT_STYLE_COLOR = "&H00FFFFFF"  # 白(未知の話者向けフォールバック)

# キャラクター立ち絵の画面上の配置(つむぎ=左下、ずんだもん=右下)と
# ファイル名の接頭辞(prepare_characters.pyの出力先と対応させる)。
CHARACTER_PREFIXES = {"つむぎ": "tsumugi", "ずんだもん": "zundamon"}
CHARACTER_POSITIONS = {"つむぎ": "left", "ずんだもん": "right"}
CHARACTER_MARGIN_X = 40

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 25
TITLE_SLIDE_DURATION_SECONDS = 2.0
# セリフとセリフの間に挿入する無音の長さ(秒)。会話らしい"間"を作るため。
PAUSE_BETWEEN_LINES_SECONDS = 0.4
SUBTITLE_FONT_SIZE = 64
SUBTITLE_MARGIN_L = 80
SUBTITLE_MARGIN_R = 80
# 字幕が使える横幅(px)。ここを超えたら折り返す。
SUBTITLE_MAX_WIDTH = VIDEO_WIDTH - SUBTITLE_MARGIN_L - SUBTITLE_MARGIN_R
_SUBTITLE_FONT_PATH = FONTS_DIR / "NotoSansJP-Bold.otf"
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


def _character_asset_paths() -> dict[str, dict[str, Path]]:
    """assets/characters/に口の開閉2状態(closed/open)が揃っているキャラクターだけ返す。

    PSD素材を用意していないユーザー向けに、片方または両方揃っていなければ
    そのキャラクターのオーバーレイは単純にスキップされる(エラーにはしない)。
    """
    assets: dict[str, dict[str, Path]] = {}
    for speaker, prefix in CHARACTER_PREFIXES.items():
        closed = CHARACTER_ASSETS_DIR / f"{prefix}_closed.png"
        open_ = CHARACTER_ASSETS_DIR / f"{prefix}_open.png"
        if closed.exists() and open_.exists():
            assets[speaker] = {"closed": closed, "open": open_}
    return assets


def _build_enable_expr(intervals: list[tuple[float, float]]) -> str:
    """between(t,s,e)の和で、ffmpegのenableオプション用の式を作る。

    区間が1つも無ければ常に偽("0")を返す。
    """
    if not intervals:
        return "0"
    return "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in intervals)


def _write_silence_wav(path: Path, duration_seconds: float, reference_wav_path: Path) -> Path:
    """reference_wav_pathと同じフォーマット(サンプルレート等)の無音WAVを作る。

    音声結合はffmpegの`-c copy`(再エンコード無し)で行うため、無音クリップも
    実際のVOICEVOX出力と完全に同じフォーマットでなければ結合できない。
    """
    with wave.open(str(reference_wav_path), "rb") as ref:
        params = ref.getparams()

    n_frames = max(0, int(round(duration_seconds * params.framerate)))
    silence_bytes = b"\x00" * (n_frames * params.nchannels * params.sampwidth)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setparams(params)
        out.writeframes(silence_bytes)
    return path


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
    pause_seconds: float = PAUSE_BETWEEN_LINES_SECONDS,
) -> tuple[list[TimedLine], list[Path]]:
    """script.mdの全セリフを音声合成し、開始/終了時刻つきのタイムラインを作る。

    script_parserで決定的にパースしたセリフを、登場順にそのままVOICEVOXへ
    渡す(voicevox_script.txtは経由しない)。これにより字幕テキスト・音声・
    シーン番号が常に一致することを保証する。

    戻り値の2つ目(audio_segments)は、タイトル区間の無音・各セリフの実音声・
    セリフ間の無音を全て含んだ「音声結合に使うファイルの並び」。この並びを
    そのまま結合すれば、字幕・スライド表示時間の計算に使うcursorと
    完全に一致した長さの音声トラックになる(音声だけがタイトル無音分早く
    始まってしまう、というズレを防ぐための設計)。
    """
    style_map = style_map or {}
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = work_dir / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    scenes = parse_script(script_text)
    if not scenes:
        raise ValueError(
            "台本からシーン・セリフを1つも抽出できませんでした。"
            "script.mdが「### シーン<N>：」見出しと「つむぎ「〜」」形式の"
            "セリフを含んでいるか確認してください。"
        )
    all_lines = flatten_lines(scenes)

    speakers = list_speakers(base_url)
    speaker_id_cache: dict[str, int] = {}

    timeline: list[TimedLine] = []
    audio_segments: list[Path] = []
    reference_audio_path: Path | None = None
    cursor = TITLE_SLIDE_DURATION_SECONDS

    for i, line in enumerate(all_lines):
        if line.speaker not in speaker_id_cache:
            style_name = style_map.get(line.speaker, DEFAULT_STYLE_NAME)
            speaker_id_cache[line.speaker] = resolve_speaker_id(speakers, line.speaker, style_name)
        speaker_id = speaker_id_cache[line.speaker]

        index = i + 1
        # 話者+セリフ本文のハッシュでキャッシュする。同じscript.mdに対して
        # render-videoを何度も再実行しても(ffmpeg側の試行錯誤などで)、
        # 内容が変わっていないセリフはVOICEVOXへの再合成をスキップする。
        cache_key = hashlib.sha256(f"{speaker_id}:{line.text}".encode("utf-8")).hexdigest()[:16]
        cached_path = cache_dir / f"{cache_key}.wav"
        audio_path = work_dir / f"line_{index:04d}.wav"

        if cached_path.exists():
            print(f"  {index:03d}: [シーン{line.scene_number}/{line.speaker}] {line.text[:30]}... (キャッシュ利用)")
            audio_path.write_bytes(cached_path.read_bytes())
        else:
            print(f"  {index:03d}: [シーン{line.scene_number}/{line.speaker}] {line.text[:30]}...")
            wav_bytes = synthesize(line.text, speaker_id, base_url)
            cached_path.write_bytes(wav_bytes)
            audio_path.write_bytes(wav_bytes)

        if reference_audio_path is None:
            reference_audio_path = audio_path

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
        audio_segments.append(audio_path)
        cursor += duration

        if i < len(all_lines) - 1:
            pause_path = work_dir / f"pause_{index:04d}.wav"
            _write_silence_wav(pause_path, pause_seconds, reference_audio_path)
            audio_segments.append(pause_path)
            cursor += pause_seconds

    if reference_audio_path is not None:
        title_silence_path = work_dir / "title_silence.wav"
        _write_silence_wav(title_silence_path, TITLE_SLIDE_DURATION_SECONDS, reference_audio_path)
        audio_segments.insert(0, title_silence_path)

    return timeline, audio_segments


_subtitle_measure_font: ImageFont.FreeTypeFont | None = None


def _get_subtitle_measure_font() -> ImageFont.FreeTypeFont:
    """字幕の折り返し判定に使うフォントを読み込む(実際に焼き込まれるBold体と同じもの)。"""
    global _subtitle_measure_font
    if _subtitle_measure_font is None:
        _subtitle_measure_font = ImageFont.truetype(str(_SUBTITLE_FONT_PATH), SUBTITLE_FONT_SIZE)
    return _subtitle_measure_font


def _wrap_subtitle_text(text: str, max_width: int = SUBTITLE_MAX_WIDTH) -> str:
    """字幕が画面の横幅に収まるよう、実測した文字幅に基づいて`\\N`で複数行に折り返す。

    ASS字幕はテキスト中に明示的な改行(`\\N`)を入れない限り自動では折り返されず、
    長いセリフをそのまま1行で渡すと画面からはみ出す(実際に発生した不具合)。
    ここでは字幕描画に使う実際のフォント(NotoSansJP-Bold)・サイズで1文字ずつ
    幅を測り、YouTubeの字幕のように画面内に収まる範囲で複数行に分割する。
    """
    font = _get_subtitle_measure_font()
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if current and dummy_draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)

    return "\\N".join(lines) if lines else text


def _build_ass_subtitle(timeline: list[TimedLine], output_path: str | Path) -> Path:
    """話者ごとに色分けしたASS字幕ファイルを生成する。

    WrapStyle: 2 を指定し、libass自身による自動折り返しを無効化した上で、
    _wrap_subtitle_text()で計算した明示的な`\\N`だけに従わせる
    (自動折り返しと手動折り返しが競合してレイアウトが乱れるのを防ぐため)。
    """
    style_lines = []
    for speaker, color in SUBTITLE_STYLE_COLORS.items():
        style_lines.append(
            f"Style: {speaker},Noto Sans JP,{SUBTITLE_FONT_SIZE},{color},&H000000FF,&H00000000,&H80000000,"
            f"-1,0,0,0,100,100,0,0,1,3,2,2,{SUBTITLE_MARGIN_L},{SUBTITLE_MARGIN_R},60,1"
        )
    style_lines.append(
        f"Style: Default,Noto Sans JP,{SUBTITLE_FONT_SIZE},{DEFAULT_STYLE_COLOR},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,3,2,2,{SUBTITLE_MARGIN_L},{SUBTITLE_MARGIN_R},60,1"
    )

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {VIDEO_WIDTH}\n"
        f"PlayResY: {VIDEO_HEIGHT}\n"
        "WrapStyle: 2\n"
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
        cleaned = item.text.replace("\n", " ").replace("{", "").replace("}", "")
        text = _wrap_subtitle_text(cleaned)
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
    timeline: list[TimedLine], slides_dir: Path, thumbnail_path: Path | None = None
) -> list[tuple[Path, float]]:
    """(スライド画像パス, 表示秒数) のリストを、シーンごとの実時間から作る。

    シーンの表示時間は「そのシーン最初のセリフの開始時刻」から「次のシーン
    最初のセリフの開始時刻」までの実時間で計算する(単純に各セリフの
    end-startを足すだけだと、セリフ間に挿入した無音の"間"がシーンの表示時間に
    反映されず、音声の総再生時間より映像が短くなってしまうため)。

    thumbnail_pathが指定されていれば、冒頭のタイトル区間はスライドの
    タイトル画面ではなくサムネイル画像そのものを表示する
    (YouTubeのサムネイルと動画の冒頭を一致させたい場合向け)。
    """
    manifest = _load_slides_manifest(slides_dir)

    title_entry = next((m for m in manifest if m["scene_number"] is None), None)
    scene_to_files: dict[int, list[str]] = {}
    for entry in manifest:
        scene_number = entry.get("scene_number")
        if scene_number is None:
            continue
        scene_to_files.setdefault(scene_number, []).append(entry["file"])

    scene_start: dict[int, float] = {}
    for item in timeline:
        if item.scene_number not in scene_start:
            scene_start[item.scene_number] = item.start

    ordered_scenes = sorted(scene_start, key=lambda sn: scene_start[sn])
    final_end = timeline[-1].end if timeline else TITLE_SLIDE_DURATION_SECONDS

    scene_duration: dict[int, float] = {}
    for i, scene_number in enumerate(ordered_scenes):
        start = scene_start[scene_number]
        end = scene_start[ordered_scenes[i + 1]] if i + 1 < len(ordered_scenes) else final_end
        scene_duration[scene_number] = end - start

    visual_timeline: list[tuple[Path, float]] = []
    if thumbnail_path and Path(thumbnail_path).exists():
        visual_timeline.append((Path(thumbnail_path), TITLE_SLIDE_DURATION_SECONDS))
    elif title_entry:
        visual_timeline.append((slides_dir / title_entry["file"], TITLE_SLIDE_DURATION_SECONDS))

    for scene_number in ordered_scenes:
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


def _write_image_concat_file(entries: list[tuple[str, float]], output_path: Path) -> Path:
    """ffmpeg concat demuxer用の画像リストファイルを書き出す(duration指定あり)。

    entries: [(ファイルパス, 表示秒数), ...]
    最後の要素はconcat demuxerの既知の挙動(最後のdurationが無視される)を
    避けるため、durationなしで同じファイルをもう一度書き足す。
    """
    lines = []
    for path, duration in entries:
        lines.append(f"file '{path}'")
        lines.append(f"duration {duration:.6f}")
    if entries:
        lines.append(f"file '{entries[-1][0]}'")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _write_audio_concat_file(paths: list[str], output_path: Path) -> Path:
    """ffmpeg concat demuxer用の音声リストファイルを書き出す(duration指定なし)。

    音声は各ファイルをそのまま連結するだけなのでdurationは不要。
    画像用と違い、末尾を重複させるトリックは絶対に行わない
    (durationを使わないこの用途で重複させると、最後のセリフの音声が
    そのまま二重に再生される不具合になる。実際に発生した不具合)。
    """
    lines = [f"file '{path}'" for path in paths]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def assemble_video(
    script_path: str | Path,
    slides_dir: str | Path,
    output_path: str | Path,
    work_dir: str | Path | None = None,
    style_map: dict[str, str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    thumbnail_path: str | Path | None = None,
) -> Path:
    """script.md + スライド画像 + VOICEVOX音声から、色分け字幕つきのmp4を組み立てる。

    thumbnail_pathを指定すると、動画冒頭のタイトル区間がスライドのタイトル
    画面ではなくサムネイル画像そのものになる(YouTubeのサムネイルと動画の
    冒頭を一致させたい場合向け)。
    """
    script_path = Path(script_path)
    slides_dir = Path(slides_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(work_dir) if work_dir else output_path.parent / "_video_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    script_text = script_path.read_text(encoding="utf-8")

    print("=== 音声合成中 ===")
    timeline, audio_segments = synthesize_timeline(script_text, work_dir / "audio", style_map, base_url)

    print("=== 字幕(ASS)を生成中 ===")
    ass_path = _build_ass_subtitle(timeline, work_dir / "captions.ass")

    print("=== スライドの表示時間を計算中 ===")
    resolved_thumbnail_path = None
    if thumbnail_path and Path(thumbnail_path).exists():
        # ffmpegのconcatデマクサーは、先頭の画像だけ解像度が異なると正しく
        # 扱えず、その画像が実質無視されてしまう不具合があった(scaleフィルタを
        # 掛けていても解消しない)。事前に動画と同じ解像度にリサイズしてから
        # 渡すことで回避する。
        resolved_thumbnail_path = work_dir / "thumbnail_for_intro.png"
        thumb_img = Image.open(thumbnail_path).convert("RGB")
        thumb_img = thumb_img.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
        thumb_img.save(resolved_thumbnail_path)

    visual_timeline = _build_visual_timeline(timeline, slides_dir, resolved_thumbnail_path)

    print("=== 音声を結合中 ===")
    audio_concat_path = _write_audio_concat_file(
        [str(path.resolve()) for path in audio_segments],
        work_dir / "audio_concat.txt",
    )
    full_audio_path = work_dir / "full_audio.wav"
    _run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(audio_concat_path), "-c", "copy", str(full_audio_path)]
    )

    print("=== スライド映像を生成中 ===")
    image_concat_path = _write_image_concat_file(
        [(str(path.resolve()), duration) for path, duration in visual_timeline],
        work_dir / "images_concat.txt",
    )
    silent_video_path = work_dir / "silent_video.mp4"
    _run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(image_concat_path),
            # サムネイル(1280x720)のように画像サイズが異なるものが混ざっても
            # 動画解像度に統一する(アスペクト比は同じ16:9なので歪みは出ない)
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},format=yuv420p",
            "-r", str(VIDEO_FPS),
            str(silent_video_path),
        ]
    )

    print("=== 音声・映像・字幕を合成中 ===")
    ass_path_arg = _quote_ffmpeg_filter_value(str(ass_path.resolve()))
    fonts_dir_arg = _quote_ffmpeg_filter_value(str(FONTS_DIR.resolve()))

    character_assets = _character_asset_paths()
    if character_assets:
        print(f"  立ち絵オーバーレイを合成します: {', '.join(character_assets)}")

    ffmpeg_inputs = ["-i", str(silent_video_path), "-i", str(full_audio_path)]
    current_label = "0:v"
    input_index = 2  # 0=映像, 1=音声。キャラクター画像はこの続きから追加する
    filter_stages: list[str] = []

    for speaker, assets in character_assets.items():
        position = CHARACTER_POSITIONS.get(speaker, "left")
        x_expr = (
            str(CHARACTER_MARGIN_X)
            if position == "left"
            else f"main_w-overlay_w-{CHARACTER_MARGIN_X}"
        )
        y_expr = "main_h-overlay_h"
        intervals = [(item.start, item.end) for item in timeline if item.speaker == speaker]
        enable_expr = _build_enable_expr(intervals)
        # サムネイルを冒頭に使う場合、サムネイル自体に既にキャラクターが
        # 描かれているため、その区間だけ動画側のオーバーレイ(常時表示の
        # 「口を閉じた」状態)を出さないようにして二重表示を避ける
        closed_enable_expr = (
            f"gte(t,{TITLE_SLIDE_DURATION_SECONDS})" if resolved_thumbnail_path else "1"
        )

        ffmpeg_inputs += ["-i", str(assets["closed"])]
        closed_idx = input_index
        input_index += 1
        ffmpeg_inputs += ["-i", str(assets["open"])]
        open_idx = input_index
        input_index += 1

        bg_closed_label = f"bg{input_index}c"
        bg_open_label = f"bg{input_index}o"
        # まず口を閉じた状態を常時オーバーレイ(=待機中のデフォルト表示)、
        # その上に口を開いた状態を、そのキャラクターが喋っている区間だけ重ねる
        filter_stages.append(
            f"[{current_label}][{closed_idx}:v]overlay=x={x_expr}:y={y_expr}:"
            f"enable='{closed_enable_expr}'[{bg_closed_label}]"
        )
        filter_stages.append(
            f"[{bg_closed_label}][{open_idx}:v]overlay=x={x_expr}:y={y_expr}:"
            f"enable='{enable_expr}'[{bg_open_label}]"
        )
        current_label = bg_open_label

    filter_stages.append(
        f"[{current_label}]ass=filename={ass_path_arg}:fontsdir={fonts_dir_arg}[vout]"
    )

    _run_ffmpeg(
        [
            *ffmpeg_inputs,
            "-filter_complex", ";".join(filter_stages),
            "-map", "[vout]", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(output_path),
        ]
    )

    print(f"\n完了: {output_path}")
    return output_path
