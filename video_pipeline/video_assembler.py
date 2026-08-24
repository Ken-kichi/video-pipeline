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
  (各セリフの実測時間 + セリフ間の間)から計算しており、この3つが
  食い違わないようにしている。動画は冒頭のタイトルスライドを挟まず、
  最初のセリフからそのまま始まる。
"""

import hashlib
import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_pipeline.script_parser import flatten_lines, parse_script
from video_pipeline.slide_image_builder import extract_shorts_text
from video_pipeline.voicevox_client import (
    DEFAULT_BASE_URL,
    DEFAULT_STYLE_NAME,
    list_speakers,
    resolve_speaker_id,
    synthesize,
)

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
CHARACTER_ASSETS_DIR = Path(__file__).parent / "assets" / "characters"
PAGE_TURN_SFX_PATH = Path(__file__).parent / "assets" / "sfx" / "page_turn.mp3"

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
# 動画本編でのキャラクター表示の高さ(px)。prepare_charactersが書き出す
# PNG(デフォルト480px)をこの高さに縮小してオーバーレイする。字幕や
# code/diagramスライドの表示領域と被らないよう、やや小さめにしている
# (実際にキャラクターが字幕・スライド内容と重なる不具合が起きたため)。
CHARACTER_VIDEO_HEIGHT = 300

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 25
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

# 静止画スライドに、ゆっくりとしたズームイン(Ken Burns風)を常時かけるための設定。
# シーン分割・スライド枚数を増やしても、1枚のスライドがその表示時間中
# 完全に無変化(ピクセル単位で1枚も動かない)だと視聴者には「止まっている」
# ように見えてしまう。常にごくわずかにズームさせることで、スライド自体の
# 枚数を増やさなくても「画面が生きている」印象を持たせる。
# ズームが強すぎると文字が読みにくくなる/わざとらしくなるため、控えめな値にする。
SLIDE_ZOOM_ENABLED = True
SLIDE_ZOOM_END_SCALE = 1.06  # 表示終了時点でのズーム倍率(1.0=無ズーム)
# zoompanフィルタは入力解像度が低いとガタつくため、いったん高解像度に
# アップスケールしてからズーム・最終解像度へダウンスケールする
_ZOOM_UPSCALE_FACTOR = 2

# BGM・ページめくり音は音声(セリフ)より小さくする。0.0〜1.0の相対音量。
BGM_VOLUME = 0.2
PAGE_TURN_VOLUME = 0.5
# BGMの開始・終了にかけるフェードの長さ(秒)。ループ再生している場合でも
# 動画全体の最初と最後にだけかける(ループの継ぎ目ごとにはかけない)。
BGM_FADE_SECONDS = 3.0


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


def character_asset_paths() -> dict[str, dict[str, Path]]:
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


def build_enable_expr(intervals: list[tuple[float, float]]) -> str:
    """between(t,s,e)の和で、ffmpegのenableオプション用の式を作る。

    区間が1つも無ければ常に偽("0")を返す。
    """
    if not intervals:
        return "0"
    return "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in intervals)


def _write_silence_wav(
    path: Path, duration_seconds: float, reference_wav_path: Path
) -> Path:
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

    戻り値の2つ目(audio_segments)は、各セリフの実音声・セリフ間の無音を
    全て含んだ「音声結合に使うファイルの並び」。この並びをそのまま結合
    すれば、字幕・スライド表示時間の計算に使うcursorと完全に一致した
    長さの音声トラックになる。
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
    cursor = 0.0

    for i, line in enumerate(all_lines):
        if line.speaker not in speaker_id_cache:
            style_name = style_map.get(line.speaker, DEFAULT_STYLE_NAME)
            speaker_id_cache[line.speaker] = resolve_speaker_id(
                speakers, line.speaker, style_name
            )
        speaker_id = speaker_id_cache[line.speaker]

        index = i + 1
        # 話者+セリフ本文のハッシュでキャッシュする。同じscript.mdに対して
        # render-videoを何度も再実行しても(ffmpeg側の試行錯誤などで)、
        # 内容が変わっていないセリフはVOICEVOXへの再合成をスキップする。
        cache_key = hashlib.sha256(
            f"{speaker_id}:{line.text}".encode("utf-8")
        ).hexdigest()[:16]
        cached_path = cache_dir / f"{cache_key}.wav"
        audio_path = work_dir / f"line_{index:04d}.wav"

        if cached_path.exists():
            print(
                f"  {index:03d}: [シーン{line.scene_number}/{line.speaker}] {line.text[:30]}... (キャッシュ利用)"
            )
            audio_path.write_bytes(cached_path.read_bytes())
        else:
            print(
                f"  {index:03d}: [シーン{line.scene_number}/{line.speaker}] {line.text[:30]}..."
            )
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

    return timeline, audio_segments


_subtitle_measure_font: ImageFont.FreeTypeFont | None = None


def _get_subtitle_measure_font() -> ImageFont.FreeTypeFont:
    """字幕の折り返し判定に使うフォントを読み込む(実際に焼き込まれるBold体と同じもの)。"""
    global _subtitle_measure_font
    if _subtitle_measure_font is None:
        _subtitle_measure_font = ImageFont.truetype(
            str(_SUBTITLE_FONT_PATH), SUBTITLE_FONT_SIZE
        )
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
        style_name = (
            item.speaker if item.speaker in SUBTITLE_STYLE_COLORS else "Default"
        )
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


def _compute_scene_boundaries(
    timeline: list[TimedLine],
) -> dict[int, tuple[float, float]]:
    """シーン番号ごとの(開始時刻, 終了時刻)を計算する。

    シーンの終了時刻は「次のシーン最初のセリフの開始時刻」（最後のシーンは
    タイムライン全体の終了時刻）とする。単純に各セリフのend-startを足すだけ
    だと、セリフ間に挿入した無音の"間"が終了時刻に反映されないため、
    render-video(スライド表示時間)・create-shorts(切り出し位置)の両方が
    この関数を共通で使う。
    """
    scene_start: dict[int, float] = {}
    for item in timeline:
        if item.scene_number not in scene_start:
            scene_start[item.scene_number] = item.start

    ordered_scenes = sorted(scene_start, key=lambda sn: scene_start[sn])
    final_end = timeline[-1].end if timeline else 0.0

    boundaries: dict[int, tuple[float, float]] = {}
    for i, scene_number in enumerate(ordered_scenes):
        start = scene_start[scene_number]
        end = (
            scene_start[ordered_scenes[i + 1]]
            if i + 1 < len(ordered_scenes)
            else final_end
        )
        boundaries[scene_number] = (start, end)
    return boundaries


def _write_scene_boundaries(timeline: list[TimedLine], output_path: Path) -> Path:
    """シーンごとの開始・終了時刻をJSONに保存する。

    create-shortsが「シーン1の終わりまで」のような正確な切り出し位置を
    再利用できるようにするため(以前は目安の秒数+無音検出で切り出し位置を
    推測していたが、BGMが流れているとセリフ間の無音が検出できず、
    結局中途半端な位置で切れてしまう不具合があった)。
    """
    boundaries = _compute_scene_boundaries(timeline)
    data = [
        {"scene_number": scene_number, "start": round(start, 3), "end": round(end, 3)}
        for scene_number, (start, end) in sorted(boundaries.items())
    ]
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path


def _write_shorts_data(
    timeline: list[TimedLine], slides_dir: Path, output_path: Path
) -> Path:
    """create-shortsが縦長レイアウトを組み立てるための素材をJSONに保存する。

    スライドはPNGとして焼き込んだ時点で文字情報が失われるため、
    manifest.jsonに残しておいたスライドの元データ(slide_image_builder.py参照)
    からshorts用の見出し・補足テキストを再構成し、シーンの開始・終了時刻と
    セットで保存する。あわせて、キャラクター立ち絵の口パクをショート動画側でも
    再現できるよう、話者ごとの発話区間(本編の口開閉オーバーレイに使ったのと
    同じタイミング)も保存する。
    """
    manifest = _load_slides_manifest(slides_dir)
    scene_to_datas: dict[int, list[dict]] = {}
    for entry in manifest:
        scene_number = entry.get("scene_number")
        if scene_number is None or "data" not in entry:
            continue
        scene_to_datas.setdefault(scene_number, []).append(entry["data"])

    boundaries = _compute_scene_boundaries(timeline)
    scenes = []
    for scene_number, (start, end) in sorted(boundaries.items()):
        datas = scene_to_datas.get(scene_number)
        # 1シーンに複数スライドが対応する場合も、ショートの短い表示時間では
        # 1シーン1見出しで十分なため、先頭のスライドのテキストだけを使う
        heading, sub_lines = extract_shorts_text(datas[0]) if datas else ("", [])
        scenes.append(
            {
                "scene_number": scene_number,
                "start": round(start, 3),
                "end": round(end, 3),
                "heading": heading,
                "sub_lines": sub_lines,
            }
        )

    speaker_timeline = [
        {"speaker": item.speaker, "start": round(item.start, 3), "end": round(item.end, 3)}
        for item in timeline
    ]

    output_path.write_text(
        json.dumps(
            {"scenes": scenes, "speaker_timeline": speaker_timeline},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path


def _build_visual_timeline(
    timeline: list[TimedLine], slides_dir: Path
) -> list[tuple[Path, float]]:
    """(スライド画像パス, 表示秒数) のリストを、シーンごとの実時間から作る。

    シーンの表示時間は「そのシーン最初のセリフの開始時刻」から「次のシーン
    最初のセリフの開始時刻」までの実時間で計算する(単純に各セリフの
    end-startを足すだけだと、セリフ間に挿入した無音の"間"がシーンの表示時間に
    反映されず、音声の総再生時間より映像が短くなってしまうため)。
    """
    manifest = _load_slides_manifest(slides_dir)

    scene_to_files: dict[int, list[str]] = {}
    for entry in manifest:
        scene_number = entry.get("scene_number")
        if scene_number is None:
            continue
        scene_to_files.setdefault(scene_number, []).append(entry["file"])

    boundaries = _compute_scene_boundaries(timeline)
    ordered_scenes = sorted(boundaries)
    scene_duration = {sn: end - start for sn, (start, end) in boundaries.items()}

    visual_timeline: list[tuple[Path, float]] = []

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


def _compute_slide_transition_times(
    visual_timeline: list[tuple[Path, float]],
) -> list[float]:
    """スライドが切り替わる時刻(2番目以降の各要素の開始時刻)のリストを返す。"""
    times: list[float] = []
    cursor = 0.0
    for i, (_, duration) in enumerate(visual_timeline):
        if i > 0:
            times.append(cursor)
        cursor += duration
    return times


def _render_zoom_clip(
    image_path: Path,
    duration: float,
    output_path: Path,
    fps: int = VIDEO_FPS,
    zoom_end_scale: float = SLIDE_ZOOM_END_SCALE,
) -> Path:
    """1枚の静止画から、ゆっくりズームインするKen Burns風の短い動画クリップを作る。

    zoompanフィルタは経験上「フレーム数(d)」基準で動くため、指定秒数分
    より少し多めのフレームを生成させ、最後に-tで正確な秒数に切り詰める
    (端数フレームでのズーム量の誤差より、秒数の正確さを優先する)。
    """
    frame_count = max(1, round(duration * fps)) + fps  # 余裕を持って多めに生成
    zoom_increment = (zoom_end_scale - 1.0) / max(1, round(duration * fps))
    upscale_w = VIDEO_WIDTH * _ZOOM_UPSCALE_FACTOR
    upscale_h = VIDEO_HEIGHT * _ZOOM_UPSCALE_FACTOR
    zoompan_filter = (
        f"scale={upscale_w}:{upscale_h}:force_original_aspect_ratio=increase,"
        f"crop={upscale_w}:{upscale_h},"
        f"zoompan=z='min(zoom+{zoom_increment:.8f},{zoom_end_scale})':"
        f"d={frame_count}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={fps}"
    )
    _run_ffmpeg(
        [
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            zoompan_filter,
            "-t",
            f"{duration:.3f}",
            "-r",
            str(fps),
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    return output_path


def _build_zoom_video_timeline(
    visual_timeline: list[tuple[Path, float]],
    work_dir: Path,
    fps: int = VIDEO_FPS,
) -> list[Path]:
    """静止画スライドのタイムラインを、Ken Burnsズーム付きの動画クリップ群に変換する。

    1枚でも生成に失敗したら、そのクリップだけ諦めて元の静止画表示
    (無ズーム)にフォールバックする(全体を止めるほどの不具合ではないため)。
    """
    clips_dir = work_dir / "_zoom_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []
    for i, (image_path, duration) in enumerate(visual_timeline):
        clip_path = clips_dir / f"clip_{i:04d}.mp4"
        try:
            _render_zoom_clip(image_path, duration, clip_path, fps=fps)
            clip_paths.append(clip_path)
        except Exception as exc:  # noqa: BLE001 1枚の失敗で全体を止めない
            print(
                f"  [警告] スライド{i}のズーム映像生成に失敗したため、"
                f"静止表示にフォールバックします: {exc}"
            )
            fallback_path = clips_dir / f"clip_{i:04d}_static.mp4"
            _run_ffmpeg(
                [
                    "-loop",
                    "1",
                    "-i",
                    str(image_path),
                    "-vf",
                    f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
                    "-t",
                    f"{duration:.3f}",
                    "-r",
                    str(fps),
                    "-pix_fmt",
                    "yuv420p",
                    str(fallback_path),
                ]
            )
            clip_paths.append(fallback_path)
    return clip_paths


def _write_video_concat_file(paths: list[Path], output_path: Path) -> Path:
    """ffmpeg concat demuxer用の動画クリップリストファイルを書き出す(duration指定なし)。

    各クリップは_render_zoom_clip側で既に正確な表示秒数に切り詰め済みのため、
    画像用のconcatファイルと違いduration指定は不要(むしろ二重に長さを
    指定すると食い違いの元になる)。
    """
    lines = [f"file '{path.resolve()}'" for path in paths]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _write_image_concat_file(
    entries: list[tuple[str, float]], output_path: Path
) -> Path:
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
    bgm_path: str | Path | None = None,
    page_turn_sound: bool = True,
) -> Path:
    """script.md + スライド画像 + VOICEVOX音声から、色分け字幕つきのmp4を組み立てる。

    動画はタイトルスライドを挟まず、冒頭から本編(解説)がそのまま始まる
    (YouTubeのサムネイルはYouTube側の設定で個別に指定するものであり、
    動画自体には焼き込まない)。

    bgm_pathを指定すると、動画全体にBGMを重ねる。動画より短ければ自動的に
    ループ再生し、動画全体の最初と最後にBGM_FADE_SECONDS秒のフェードイン/
    アウトをかける。page_turn_sound=Trueの場合、スライドが切り替わる
    タイミングでassets/sfx/page_turn.mp3を鳴らす。BGM・ページめくり音は
    どちらもセリフの音声より小さい音量(BGM_VOLUME/PAGE_TURN_VOLUME)にする。
    """
    script_path = Path(script_path)
    slides_dir = Path(slides_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(work_dir) if work_dir else output_path.parent / "_video_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    script_text = script_path.read_text(encoding="utf-8")

    print("=== 音声合成中 ===")
    timeline, audio_segments = synthesize_timeline(
        script_text, work_dir / "audio", style_map, base_url
    )

    print("=== 字幕(ASS)を生成中 ===")
    ass_path = _build_ass_subtitle(timeline, work_dir / "captions.ass")

    print("=== スライドの表示時間を計算中 ===")
    visual_timeline = _build_visual_timeline(timeline, slides_dir)

    scene_boundaries_path = _write_scene_boundaries(
        timeline, output_path.parent / "scene_boundaries.json"
    )
    print(f"  シーン境界を保存しました: {scene_boundaries_path}")

    shorts_data_path = _write_shorts_data(
        timeline, slides_dir, output_path.parent / "shorts_data.json"
    )
    print(f"  ショート用データを保存しました: {shorts_data_path}")

    print("=== 音声を結合中 ===")
    audio_concat_path = _write_audio_concat_file(
        [str(path.resolve()) for path in audio_segments],
        work_dir / "audio_concat.txt",
    )
    full_audio_path = work_dir / "full_audio.wav"
    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(audio_concat_path),
            "-c",
            "copy",
            str(full_audio_path),
        ]
    )

    print("=== スライド映像を生成中 ===")
    silent_video_path = work_dir / "silent_video.mp4"
    if SLIDE_ZOOM_ENABLED:
        # 各スライドをKen Burns風のゆっくりズーム動画クリップに変換してから
        # 連結する(完全な静止画のまま並べると、シーンを増やしても表示時間中
        # 画面が一切動かず「止まっている」印象を与えてしまうため)
        print("  各スライドにゆっくりズームを適用中(Ken Burns風)...")
        zoom_clip_paths = _build_zoom_video_timeline(visual_timeline, work_dir)
        video_concat_path = _write_video_concat_file(
            zoom_clip_paths, work_dir / "video_clips_concat.txt"
        )
        _run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(video_concat_path),
                "-c",
                "copy",
                str(silent_video_path),
            ]
        )
    else:
        image_concat_path = _write_image_concat_file(
            [(str(path.resolve()), duration) for path, duration in visual_timeline],
            work_dir / "images_concat.txt",
        )
        _run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(image_concat_path),
                # スライド画像の解像度を動画解像度に統一する
                "-vf",
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},format=yuv420p",
                "-r",
                str(VIDEO_FPS),
                str(silent_video_path),
            ]
        )

    print("=== 音声・映像・字幕を合成中 ===")
    ass_path_arg = _quote_ffmpeg_filter_value(str(ass_path.resolve()))
    fonts_dir_arg = _quote_ffmpeg_filter_value(str(FONTS_DIR.resolve()))

    character_assets = character_asset_paths()
    if character_assets:
        print(f"  立ち絵オーバーレイを合成します: {', '.join(character_assets)}")

    total_duration = _wav_duration_seconds(full_audio_path)

    ffmpeg_inputs = ["-i", str(silent_video_path), "-i", str(full_audio_path)]
    current_label = "0:v"
    input_index = 2  # 0=映像, 1=音声。キャラクター画像・BGM・SEはこの続きから追加する
    filter_stages: list[str] = []

    for speaker, assets in character_assets.items():
        position = CHARACTER_POSITIONS.get(speaker, "left")
        x_expr = (
            str(CHARACTER_MARGIN_X)
            if position == "left"
            else f"main_w-overlay_w-{CHARACTER_MARGIN_X}"
        )
        y_expr = "main_h-overlay_h"
        intervals = [
            (item.start, item.end) for item in timeline if item.speaker == speaker
        ]
        enable_expr = build_enable_expr(intervals)

        ffmpeg_inputs += ["-i", str(assets["closed"])]
        closed_idx = input_index
        input_index += 1
        ffmpeg_inputs += ["-i", str(assets["open"])]
        open_idx = input_index
        input_index += 1

        closed_scaled_label = f"charc{closed_idx}"
        open_scaled_label = f"charo{open_idx}"
        filter_stages.append(
            f"[{closed_idx}:v]scale=-2:{CHARACTER_VIDEO_HEIGHT}[{closed_scaled_label}]"
        )
        filter_stages.append(
            f"[{open_idx}:v]scale=-2:{CHARACTER_VIDEO_HEIGHT}[{open_scaled_label}]"
        )

        bg_closed_label = f"bg{input_index}c"
        bg_open_label = f"bg{input_index}o"
        # まず口を閉じた状態を常時オーバーレイ(=待機中のデフォルト表示)、
        # その上に口を開いた状態を、そのキャラクターが喋っている区間だけ重ねる
        filter_stages.append(
            f"[{current_label}][{closed_scaled_label}]overlay=x={x_expr}:y={y_expr}[{bg_closed_label}]"
        )
        filter_stages.append(
            f"[{bg_closed_label}][{open_scaled_label}]overlay=x={x_expr}:y={y_expr}:"
            f"enable='{enable_expr}'[{bg_open_label}]"
        )
        current_label = bg_open_label

    filter_stages.append(
        f"[{current_label}]ass=filename={ass_path_arg}:fontsdir={fonts_dir_arg}[vout]"
    )

    # --- 音声側: セリフに加えてBGM・ページめくり音を混ぜる ---
    audio_labels = ["1:a:0"]

    if bgm_path and Path(bgm_path).exists():
        print(f"  BGMを合成します: {bgm_path}")
        # -stream_loop -1 で無限ループ入力にし、atrimで動画の長さぴったりに
        # 切り詰める(=BGMが動画より短くても自動的に連続再生される)
        ffmpeg_inputs += ["-stream_loop", "-1", "-i", str(Path(bgm_path).resolve())]
        bgm_idx = input_index
        input_index += 1
        fade_out_start = max(0.0, total_duration - BGM_FADE_SECONDS)
        filter_stages.append(
            f"[{bgm_idx}:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={BGM_FADE_SECONDS},"
            f"afade=t=out:st={fade_out_start:.3f}:d={BGM_FADE_SECONDS},"
            f"volume={BGM_VOLUME}[bgm]"
        )
        audio_labels.append("bgm")
    elif bgm_path:
        print(
            f"  [警告] BGMファイルが見つかりません: {bgm_path}（BGM無しで続行します）"
        )

    transition_times = (
        _compute_slide_transition_times(visual_timeline) if page_turn_sound else []
    )
    if transition_times and PAGE_TURN_SFX_PATH.exists():
        print(f"  ページめくり音を{len(transition_times)}箇所に合成します")
        ffmpeg_inputs += ["-i", str(PAGE_TURN_SFX_PATH)]
        sfx_idx = input_index
        input_index += 1

        if len(transition_times) == 1:
            delay_ms = int(transition_times[0] * 1000)
            filter_stages.append(
                f"[{sfx_idx}:a]adelay={delay_ms}:all=1,volume={PAGE_TURN_VOLUME}[se_mixed]"
            )
        else:
            split_labels = "".join(f"[se{i}]" for i in range(len(transition_times)))
            filter_stages.append(
                f"[{sfx_idx}:a]asplit={len(transition_times)}{split_labels}"
            )
            delayed_refs = []
            for i, t in enumerate(transition_times):
                delay_ms = int(t * 1000)
                filter_stages.append(f"[se{i}]adelay={delay_ms}:all=1[sed{i}]")
                delayed_refs.append(f"[sed{i}]")
            filter_stages.append(
                "".join(delayed_refs)
                + f"amix=inputs={len(transition_times)}:duration=longest:normalize=0,"
                f"volume={PAGE_TURN_VOLUME}[se_mixed]"
            )
        audio_labels.append("se_mixed")

    if len(audio_labels) == 1:
        audio_map_args = ["-map", audio_labels[0]]
    else:
        mix_inputs = "".join(f"[{label}]" for label in audio_labels)
        filter_stages.append(
            f"{mix_inputs}amix=inputs={len(audio_labels)}:duration=first:normalize=0[aout]"
        )
        audio_map_args = ["-map", "[aout]"]

    _run_ffmpeg(
        [
            *ffmpeg_inputs,
            "-filter_complex",
            ";".join(filter_stages),
            "-map",
            "[vout]",
            *audio_map_args,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
    )

    print(f"\n完了: {output_path}")
    return output_path
