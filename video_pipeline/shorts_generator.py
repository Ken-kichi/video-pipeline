"""横長(16:9)の完成動画から、YouTubeショート(9:16)を切り出す。

縦長キャンバスは3段構成にする:
  - 上段(HOOK_BAR_HEIGHT): shorts_agentが生成するフック文言を常時表示する帯
  - 中段(SLIDE_AREA_HEIGHT): 対応するシーンのスライド見出し・箇条書きを、
    ショート動画でも読めるサイズに拡大して表示する。シーンが切り替わるたびに
    表示内容も切り替わる
  - 下段(BOTTOM_HALF_HEIGHT): つむぎ・ずんだもんの立ち絵を、本編と同じ
    口開閉オーバーレイ(発話区間に合わせて口が動く)で配置する

以前は「完成動画の冒頭をそのまま縮小してセンターに置き、上下の空いた
スペースに文言を入れる」という構成だったが、実際の16:9映像を縮小すると
スマホ画面上でスライドの文字がほとんど読めなくなる問題があったため、
スライドの文字情報とキャラクターの立ち絵を、縦長キャンバス向けに直接
大きく再構成する方式に変更した。

このためには、シーンごとのスライド見出し・箇条書きテキストと、
キャラクターごとの発話区間(本編の口パクに使ったタイミングと同じもの)が
必要になる。これらはrender-video(video_assembler.assemble_video)が
書き出すshorts_data.jsonから読む。古いバージョンで生成した動画には
このファイルが無いため、ショート化するにはrender-videoを最新版で
再実行する必要がある(過去動画への遡及対応はしない)。

切り出す区間の決め方(何秒目までを使うか)は変更していない: script_agentが
台本の0:00〜1:00を「単体でショートとして成立する概要パート」として生成する
設計になっているため、これに合わせてデフォルト60秒。create_shorts.pyの
デフォルトでは、台本の見出しから概要パート最後のシーン番号を自動検出し、
scene_boundaries.jsonがあればそのシーンの正確な終了時刻を使う。
scene_boundaries.jsonが無い場合はdurationを目安秒数として使うが、
指定秒数ぴったりで切ると、セリフの途中で途切れてしまう不具合が実際に
発生したため、指定秒数付近の無音区間(セリフの間)を検出し、そこに合わせて
実際の切り出し秒数を調整する。ただし先頭のタイトル/サムネイル静止区間
(TITLE_SLIDE_DURATION_SECONDS)はスキップし、本編の動きが始まる最初の
シーンから切り出す(静止画始まりはショートの「視聴を選択した割合」を
大きく下げることが実際のアナリティクスで確認されたため)。
"""

import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_pipeline.video_assembler import (
    CHARACTER_MARGIN_X,
    CHARACTER_POSITIONS,
    CHARACTER_PREFIXES,
    TITLE_SLIDE_DURATION_SECONDS,
    VIDEO_FPS,
    build_enable_expr,
    character_asset_paths,
)

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
TOP_HALF_HEIGHT = SHORTS_HEIGHT // 2
BOTTOM_HALF_HEIGHT = SHORTS_HEIGHT - TOP_HALF_HEIGHT
# 上段(フック文言)の高さ。残りをスライド文字エリアに割り当てる。
HOOK_BAR_HEIGHT = 150
SLIDE_AREA_HEIGHT = TOP_HALF_HEIGHT - HOOK_BAR_HEIGHT
# 下段(キャラクター)の表示高さ。立ち絵は縦横比がほぼ1:1(トリミング後)なので、
# 高さをそのまま使うと2人並べたときに1080px幅に収まらず重なってしまう。
# 左右マージン(CHARACTER_MARGIN_X)を差し引いても2人が重ならない高さにする。
CHARACTER_SHORTS_HEIGHT = 480

DEFAULT_SHORTS_DURATION_SECONDS = 60.0
# ショート動画だけにかける再生速度倍率(視聴維持率を意識して本編より速く見せる)
DEFAULT_SHORTS_SPEED = 1.5
# 指定秒数の前後何秒まで無音区間(自然な切れ目)を探すか
CUTOFF_SEARCH_WINDOW_SECONDS = 4.0
# 無音とみなす音量閾値・最低継続時間(ffmpeg silencedetect用)
SILENCE_NOISE_THRESHOLD_DB = -30
SILENCE_MIN_DURATION_SECONDS = 0.15
# 自然な切れ目が見つからない場合でも、末尾にかける音声フェードアウトの長さ
END_FADE_SECONDS = 0.3

_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Bold.otf"
BAR_BG_COLOR = (26, 26, 46)  # 動画本編のACCENT_COLORに近い、目に馴染むダークカラー
TEXT_COLOR = "#FFFFFF"
TEXT_OUTLINE_COLOR = "#1A1A2E"
SUB_TEXT_COLOR = "#FFE066"

HOOK_FONT_SIZE = 80
SLIDE_HEADING_FONT_SIZE = 84
SLIDE_SUB_FONT_SIZE = 48
SLIDE_TEXT_MIN_FONT_SIZE = 28
SLIDE_TEXT_MARGIN_X = 70


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_PATH), size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """日本語向けに1文字ずつ幅を測って折り返す簡易ワードラップ。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        trial = current + ch
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def _build_text_bar(text: str, width: int, height: int, font_size: int, fill: str) -> Image.Image:
    """指定したサイズの帯に、縁取り付きの中央揃えテキストを描画する。

    長い文言で帯の幅に収まらない場合は自動でフォントサイズを下げるが、
    それでも収まらないほど長い場合は2行に折り返す。
    """
    img = Image.new("RGB", (width, height), BAR_BG_COLOR)
    draw = ImageDraw.Draw(img)
    if not text:
        return img

    max_width = width - 80
    font = _load_font(font_size)
    while font.size > 40 and draw.textlength(text, font=font) > max_width:
        font = _load_font(font.size - 6)

    # それでも収まらない場合は中央で2行に折り返す
    lines = [text]
    if draw.textlength(text, font=font) > max_width and len(text) > 4:
        mid = len(text) // 2
        lines = [text[:mid], text[mid:]]

    line_height = int(font.size * 1.15)
    total_height = line_height * len(lines)
    y = (height - total_height) / 2
    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = (width - line_width) / 2
        draw.text((x, y), line, font=font, fill=fill, stroke_width=8, stroke_fill=TEXT_OUTLINE_COLOR)
        y += line_height
    return img


def _build_slide_text_image(heading: str, sub_lines: list[str], width: int, height: int) -> Image.Image:
    """シーンのスライド見出し・補足行を、ショート動画の帯に大きく描画する。

    帯の高さに収まらない場合は見出し・補足のフォントサイズを一緒に縮小し、
    それでも収まらない場合は補足行を末尾から間引く(comparisonなど元々
    テキスト量が多いレイアウトでも、必ず帯の高さ内に収める)。
    """
    img = Image.new("RGB", (width, height), BAR_BG_COLOR)
    draw = ImageDraw.Draw(img)
    if not heading and not sub_lines:
        return img

    max_width = width - SLIDE_TEXT_MARGIN_X * 2
    heading_size = SLIDE_HEADING_FONT_SIZE
    sub_size = SLIDE_SUB_FONT_SIZE
    lines = list(sub_lines)

    while True:
        heading_font = _load_font(heading_size)
        sub_font = _load_font(sub_size)
        heading_lines = _wrap_text(draw, heading, heading_font, max_width) if heading else []
        sub_wrapped: list[str] = []
        for line in lines:
            sub_wrapped.extend(_wrap_text(draw, f"・{line}", sub_font, max_width))

        heading_line_height = int(heading_size * 1.25)
        sub_line_height = int(sub_size * 1.3)
        gap = 30 if heading_lines and sub_wrapped else 0
        total_height = heading_line_height * len(heading_lines) + gap + sub_line_height * len(sub_wrapped)

        if total_height <= height:
            break
        if heading_size > SLIDE_TEXT_MIN_FONT_SIZE:
            heading_size -= 4
            sub_size = max(sub_size - 2, 18)
        elif lines:
            lines = lines[:-1]
        else:
            break

    y = (height - total_height) / 2
    for line in heading_lines:
        line_width = draw.textlength(line, font=heading_font)
        draw.text(
            ((width - line_width) / 2, y),
            line,
            font=heading_font,
            fill=TEXT_COLOR,
            stroke_width=6,
            stroke_fill=TEXT_OUTLINE_COLOR,
        )
        y += heading_line_height
    y += gap
    for line in sub_wrapped:
        draw.text(
            (SLIDE_TEXT_MARGIN_X, y),
            line,
            font=sub_font,
            fill=SUB_TEXT_COLOR,
            stroke_width=4,
            stroke_fill=TEXT_OUTLINE_COLOR,
        )
        y += sub_line_height

    return img


def read_scene_end_time(scene_boundaries_path: str | Path, scene_number: int) -> float | None:
    """render-videoが書き出したscene_boundaries.jsonから、指定シーンの終了時刻を読む。

    見つかれば正確な秒数、見つからなければNoneを返す(呼び出し側は
    Noneの場合、目安の秒数+無音検出にフォールバックする)。
    """
    scene_boundaries_path = Path(scene_boundaries_path)
    if not scene_boundaries_path.exists():
        return None
    try:
        data = json.loads(scene_boundaries_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    for entry in data:
        if entry.get("scene_number") == scene_number:
            return float(entry["end"])
    return None


_OVERVIEW_SCENE_HEADING_RE = re.compile(r"^### シーン(\d+)[：:].*概要パート", re.MULTILINE)


def find_overview_end_scene(script_text: str) -> int:
    """台本から「概要パート」最後のシーン番号を求める。

    script_agentの設計上、概要パート(0:00〜1:00)は単体でショートとして
    成立するように8〜10個程度の短いシーンに分割され、各シーンの見出しに
    「概要パート」という文言が入る(例:「### シーン3：概要パート（0:14〜0:21）」)。
    この文言を含む見出しのうち、最後のシーン番号を返す。見出しが見つからない
    場合(古い形式の台本など)は1を返す。
    """
    scene_numbers = [int(m) for m in _OVERVIEW_SCENE_HEADING_RE.findall(script_text)]
    return max(scene_numbers) if scene_numbers else 1


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpegの実行に失敗しました。ffmpegがインストールされているか確認してください。\n"
            f"エラー出力:\n{result.stderr[-4000:]}"
        )
    return result


def _find_natural_cutoff(
    video_path: str | Path,
    target_duration: float,
    search_window: float = CUTOFF_SEARCH_WINDOW_SECONDS,
) -> float:
    """target_duration付近の無音区間(セリフの間)を探し、そこに合わせた秒数を返す。

    見つからなければtarget_durationをそのまま返す(呼び出し側で音声フェード
    アウトをかけて急な切れ方を緩和する)。指定秒数ぴったりで切ると、
    セリフの途中で途切れてしまう不具合が実際に発生したための対策。
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-af",
            f"silencedetect=noise={SILENCE_NOISE_THRESHOLD_DB}dB:"
            f"duration={SILENCE_MIN_DURATION_SECONDS}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    stderr = result.stderr

    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", stderr)]

    candidates: list[float] = []
    for start in starts:
        if abs(start - target_duration) <= search_window:
            candidates.append(start)
    for start, end in zip(starts, ends):
        mid = (start + end) / 2
        if abs(mid - target_duration) <= search_window:
            candidates.append(mid)

    if not candidates:
        return target_duration
    return min(candidates, key=lambda c: abs(c - target_duration))


def _rebase_interval(
    start: float, end: float, window_start: float, window_end: float, speed: float
) -> tuple[float, float] | None:
    """絶対時刻の区間を、切り出し・速度変換後のショート内ローカル時刻に変換する。

    [window_start, window_end)と重ならない区間はNoneを返す(呼び出し側で除外する)。
    """
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    if clipped_end <= clipped_start:
        return None
    return ((clipped_start - window_start) / speed, (clipped_end - window_start) / speed)


def _build_slide_text_video(
    scenes: list[dict],
    window_start: float,
    window_end: float,
    speed: float,
    output_duration: float,
    work_dir: Path,
) -> Path:
    """シーンごとのスライド見出し・箇条書きを、切り替わる縦長映像として書き出す。"""
    clips_dir = work_dir / "_shorts_slide_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    segments: list[tuple[str, list[str], float, float]] = []
    for scene in sorted(scenes, key=lambda s: s["start"]):
        rebased = _rebase_interval(scene["start"], scene["end"], window_start, window_end, speed)
        if rebased is None:
            continue
        local_start, local_end = rebased
        segments.append((scene.get("heading", ""), scene.get("sub_lines", []), local_start, local_end))

    if not segments:
        raise RuntimeError(
            "ショートの対象区間に該当するシーンがshorts_data.jsonにありません。"
            "--durationや--sceneの指定を見直してください。"
        )

    # 端数を、切り出し区間の先頭・末尾ぴったりに合わせる(内部の境界は
    # 同じ変換で計算しているため、両端さえ揃えば連続性は保たれる)
    heading0, subs0, _, end0 = segments[0]
    segments[0] = (heading0, subs0, 0.0, end0)
    heading_n, subs_n, start_n, _ = segments[-1]
    segments[-1] = (heading_n, subs_n, start_n, output_duration)

    clip_paths: list[Path] = []
    for i, (heading, sub_lines, local_start, local_end) in enumerate(segments):
        clip_duration = local_end - local_start
        if clip_duration <= 0.02:
            continue
        image = _build_slide_text_image(heading, sub_lines, SHORTS_WIDTH, SLIDE_AREA_HEIGHT)
        image_path = clips_dir / f"slide_{i:03d}.png"
        image.save(image_path)
        clip_path = clips_dir / f"slide_{i:03d}.mp4"
        _run_ffmpeg(
            [
                "-loop",
                "1",
                "-t",
                f"{clip_duration:.3f}",
                "-i",
                str(image_path),
                "-r",
                str(VIDEO_FPS),
                "-pix_fmt",
                "yuv420p",
                str(clip_path),
            ]
        )
        clip_paths.append(clip_path)

    concat_path = clips_dir / "concat.txt"
    concat_path.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in clip_paths) + "\n", encoding="utf-8"
    )
    out_path = work_dir / "slide_text.mp4"
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", str(out_path)])
    return out_path


def _crop_character_image(image_path: Path, work_dir: Path) -> Path:
    """立ち絵PNGの透明な余白をトリミングする。

    本編の口パクオーバーレイ(CHARACTER_VIDEO_HEIGHT=300)では気にならなかった
    頭上の透明マージンが、ショートの下段(CHARACTER_SHORTS_HEIGHT=880)まで
    拡大すると、キャラクターの頭と中段の間に不自然な空白として目立って
    しまう(実際にレンダリングして確認した)。中身のbounding boxで
    トリミングしてから使うことで、割り当てた高さいっぱいにキャラクターが
    表示されるようにする。
    """
    cache_dir = work_dir / "_cropped_characters"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cropped_path = cache_dir / image_path.name
    if not cropped_path.exists():
        img = Image.open(image_path)
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        img.save(cropped_path)
    return cropped_path


def _build_character_video(
    speaker_timeline: list[dict],
    window_start: float,
    window_end: float,
    speed: float,
    output_duration: float,
    work_dir: Path,
) -> Path:
    """つむぎ・ずんだもんの立ち絵を、本編と同じ口開閉オーバーレイで下段映像として書き出す。"""
    out_path = work_dir / "characters.mp4"
    assets = character_asset_paths()
    bg_hex = f"0x{BAR_BG_COLOR[0]:02x}{BAR_BG_COLOR[1]:02x}{BAR_BG_COLOR[2]:02x}"

    if not assets:
        print("  [警告] assets/characters/にキャラクター立ち絵が見つかりません。無地の背景で代用します")
        _run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c={bg_hex}:s={SHORTS_WIDTH}x{BOTTOM_HALF_HEIGHT}:d={output_duration:.3f}:r={VIDEO_FPS}",
                "-pix_fmt",
                "yuv420p",
                "-t",
                f"{output_duration:.3f}",
                str(out_path),
            ]
        )
        return out_path

    intervals_by_speaker: dict[str, list[tuple[float, float]]] = {}
    for item in speaker_timeline:
        rebased = _rebase_interval(item["start"], item["end"], window_start, window_end, speed)
        if rebased is None:
            continue
        intervals_by_speaker.setdefault(item["speaker"], []).append(rebased)

    ffmpeg_inputs: list[str] = []
    input_index = 0
    filter_stages: list[str] = [
        f"color=c={bg_hex}:s={SHORTS_WIDTH}x{BOTTOM_HALF_HEIGHT}:d={output_duration:.3f}:r={VIDEO_FPS}[bg0]"
    ]
    current_label = "bg0"
    stage = 0
    for speaker in CHARACTER_PREFIXES:
        if speaker not in assets:
            continue
        position = CHARACTER_POSITIONS.get(speaker, "left")
        x_expr = (
            str(CHARACTER_MARGIN_X)
            if position == "left"
            else f"main_w-overlay_w-{CHARACTER_MARGIN_X}"
        )
        y_expr = "main_h-overlay_h"
        enable_expr = build_enable_expr(intervals_by_speaker.get(speaker, []))

        closed_path = _crop_character_image(assets[speaker]["closed"], work_dir)
        open_path = _crop_character_image(assets[speaker]["open"], work_dir)

        ffmpeg_inputs += ["-loop", "1", "-i", str(closed_path)]
        closed_idx = input_index
        input_index += 1
        ffmpeg_inputs += ["-loop", "1", "-i", str(open_path)]
        open_idx = input_index
        input_index += 1

        closed_label = f"c{stage}"
        open_label = f"o{stage}"
        filter_stages.append(f"[{closed_idx}:v]scale=-2:{CHARACTER_SHORTS_HEIGHT}[{closed_label}]")
        filter_stages.append(f"[{open_idx}:v]scale=-2:{CHARACTER_SHORTS_HEIGHT}[{open_label}]")

        closed_out = f"bg{stage + 1}c"
        filter_stages.append(
            f"[{current_label}][{closed_label}]overlay=x={x_expr}:y={y_expr}:enable='1'[{closed_out}]"
        )
        open_out = f"bg{stage + 1}o"
        filter_stages.append(
            f"[{closed_out}][{open_label}]overlay=x={x_expr}:y={y_expr}:enable='{enable_expr}'[{open_out}]"
        )
        current_label = open_out
        stage += 1

    filter_stages.append(f"[{current_label}]format=yuv420p[vout]")

    _run_ffmpeg(
        [
            *ffmpeg_inputs,
            "-filter_complex",
            ";".join(filter_stages),
            "-map",
            "[vout]",
            "-r",
            str(VIDEO_FPS),
            "-t",
            f"{output_duration:.3f}",
            str(out_path),
        ]
    )
    return out_path


def build_shorts_video(
    source_video_path: str | Path,
    output_path: str | Path,
    hook_text: str,
    shorts_data_path: str | Path | None = None,
    duration: float = DEFAULT_SHORTS_DURATION_SECONDS,
    exact_end_time: float | None = None,
    work_dir: str | Path | None = None,
    speed: float = DEFAULT_SHORTS_SPEED,
    start_offset: float = TITLE_SLIDE_DURATION_SECONDS,
) -> Path:
    """完成動画の冒頭を切り出し、上段=フック文言/中段=スライド文字/下段=キャラクター
    の9:16ショート動画として書き出す。

    shorts_data_pathは、render-video(video_assembler.assemble_video)が
    書き出すshorts_data.json(シーンごとの見出し・箇条書きと、話者ごとの
    発話区間)。省略時はsource_video_pathと同じディレクトリのものを使う。
    このファイルが無い動画(古いバージョンで生成した動画)はショート化できない。

    exact_end_time・durationの意味、末尾フェード、speedの扱いは以前と同じ
    (詳細はモジュールdocstring参照)。
    """
    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_shorts_data_path = (
        Path(shorts_data_path) if shorts_data_path else source_video_path.parent / "shorts_data.json"
    )
    if not resolved_shorts_data_path.exists():
        raise FileNotFoundError(
            f"{resolved_shorts_data_path} が見つかりません。上段にフック文言、"
            "中段にスライド文字、下段にキャラクターを配置する新レイアウトには"
            "render-videoが書き出すshorts_data.jsonが必要です。"
            "render-videoを最新版で再実行して動画を作り直してください"
            "(過去に生成した動画は非対応です)。"
        )
    shorts_data = json.loads(resolved_shorts_data_path.read_text(encoding="utf-8"))
    scenes = shorts_data.get("scenes", [])
    speaker_timeline = shorts_data.get("speaker_timeline", [])

    work_dir = Path(work_dir) if work_dir else output_path.parent / "_shorts_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    if exact_end_time is not None:
        actual_duration = exact_end_time
        print(f"  シーン境界に基づき、正確に{actual_duration:.2f}秒で切り出します")
    else:
        actual_duration = _find_natural_cutoff(source_video_path, duration)
        if abs(actual_duration - duration) > 0.05:
            print(
                f"  指定秒数({duration:.1f}秒)付近の自然な切れ目"
                f"({actual_duration:.2f}秒)に合わせて調整しました"
            )

    # 切り出す長さに対して静止区間が長すぎる(極端に短い動画など)場合は
    # スキップせず先頭から使う
    if start_offset >= actual_duration:
        start_offset = 0.0
    trimmed_duration = actual_duration - start_offset
    if start_offset > 0:
        print(f"  冒頭の静止区間{start_offset:.2f}秒をスキップして切り出します")

    output_duration = trimmed_duration / speed

    print("  上段: フック文言バーを作成中")
    hook_bar = _build_text_bar(hook_text, SHORTS_WIDTH, HOOK_BAR_HEIGHT, HOOK_FONT_SIZE, TEXT_COLOR)
    hook_bar_path = work_dir / "hook_bar.png"
    hook_bar.save(hook_bar_path)

    print("  中段: スライド文字パートを作成中")
    slide_text_path = _build_slide_text_video(
        scenes, start_offset, actual_duration, speed, output_duration, work_dir
    )

    print("  下段: キャラクターパートを作成中")
    characters_path = _build_character_video(
        speaker_timeline, start_offset, actual_duration, speed, output_duration, work_dir
    )

    print("  3段を合成中")
    fade_start = max(0.0, output_duration - END_FADE_SECONDS)
    filter_complex = (
        "[0:v]format=yuv420p[hook];"
        "[1:v]format=yuv420p[slide];"
        "[2:v]format=yuv420p[chars];"
        "[hook][slide][chars]vstack=inputs=3[vout];"
        f"[3:a]atempo={speed}[a_fast];"
        # 自然な切れ目にほぼ合わせているとはいえ、確実に滑らかに終わらせるため
        # 末尾に短いフェードアウトを常にかける(速度変換後の尺を基準に計算)
        f"[a_fast]afade=t=out:st={fade_start:.3f}:d={END_FADE_SECONDS}[aout]"
    )

    _run_ffmpeg(
        [
            "-loop",
            "1",
            "-r",
            str(VIDEO_FPS),
            "-t",
            f"{output_duration:.3f}",
            "-i",
            str(hook_bar_path),
            "-i",
            str(slide_text_path),
            "-i",
            str(characters_path),
            "-ss",
            f"{start_offset:.3f}",
            "-t",
            f"{trimmed_duration:.3f}",
            "-i",
            str(source_video_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-t",
            f"{output_duration:.3f}",
            str(output_path),
        ]
    )

    return output_path
