"""横長(16:9)の完成動画から、YouTubeショート(9:16)を切り出す。

以前は「動画冒頭をカットし、Canvaで縦長キャンバスの中央に貼り付け、
上下の空いたスペースに文言を入れる」という作業を手動で行っていた。
これを自動化する:
  1. 完成動画(final_video.mp4)の冒頭付近(デフォルト60秒。script_agentが
     台本の0:00〜1:00を「単体でショートとして成立する概要パート」として
     生成する設計になっているため、これに合わせている)を切り出す。
     指定秒数ぴったりで切ると、セリフの途中で途切れてしまう不具合が
     実際に発生したため、指定秒数付近の無音区間(セリフの間)を検出し、
     そこに合わせて実際の切り出し秒数を調整する
  2. 9:16の縦長キャンバスの中央に、元の16:9映像を配置する
  3. 上下の余白に、shorts_agentが生成するフック文言を大きく焼き込む
"""

import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
# 元動画(16:9)をSHORTS_WIDTHに合わせて縮小した高さ。エンコードの都合上偶数にする。
_SCALED_VIDEO_HEIGHT = round(SHORTS_WIDTH * 9 / 16 / 2) * 2
BAR_HEIGHT = (SHORTS_HEIGHT - _SCALED_VIDEO_HEIGHT) // 2

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
# 上下バーの初期フォントサイズ(帯の高さ656pxに対して大きくインパクトを
# 持たせる。文字が帯の幅に収まらない場合は_build_text_bar内で自動縮小する)
TOP_FONT_SIZE = 140
BOTTOM_FONT_SIZE = 90


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_PATH), size)


def _build_text_bar(
    text: str, width: int, height: int, font_size: int, fill: str
) -> Image.Image:
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
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=8,
            stroke_fill=TEXT_OUTLINE_COLOR,
        )
        y += line_height
    return img


def read_scene_end_time(
    scene_boundaries_path: str | Path, scene_number: int
) -> float | None:
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


def find_overview_end_scene(script_text: str) -> int:
    """台本から「概要パート」最後のシーン番号を求める。

    script_agentの設計上、概要パート(0:00〜1:00)は単体でショートとして
    成立するように4〜6個の短いシーンに分割され、区切りの`---`行を挟んで
    詳細解説パートへ続く。以前は「シーン1の終わり」を決め打ちで切り出して
    いたが、この分割ルール導入後はシーン1だけでは概要パートの途中(結論を
    言う前)で切れてしまうため、`---`より前にある最後のシーン番号を都度読む。
    区切りが見つからない場合(概要パートが1シーンのみの旧形式など)は1を返す。
    """
    overview_text = re.split(r"(?m)^---\s*$", script_text, maxsplit=1)[0]
    scene_numbers = [int(m) for m in re.findall(r"(?m)^### シーン(\d+)", overview_text)]
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


def build_shorts_video(
    source_video_path: str | Path,
    output_path: str | Path,
    main_text: str,
    sub_text: str,
    duration: float = DEFAULT_SHORTS_DURATION_SECONDS,
    exact_end_time: float | None = None,
    work_dir: str | Path | None = None,
    speed: float = DEFAULT_SHORTS_SPEED,
) -> Path:
    """完成動画の冒頭を切り出し、9:16のショート動画として書き出す。

    上段にmain_text、下段にsub_textを表示する。

    exact_end_timeが指定されていれば、それを切り出し秒数としてそのまま使う
    (render-videoが書き出したscene_boundaries.jsonから読んだ、シーンの
    正確な終了時刻を渡す想定)。指定が無い場合はdurationを目安として、
    その付近の無音区間(セリフの間)を探して調整する
    (BGMを使っている場合、セリフ間の無音がBGMの音でかき消されて検出できず、
    結局目安の秒数ぴったりで切れてしまうことがある。scene_boundaries.jsonが
    使える場合は必ずそちらを優先すること)。
    いずれの場合も、末尾には短い音声フェードアウトをかける。

    speedは本編にはかけないショート動画だけの再生速度倍率(視聴維持率対策)。
    切り出し区間(actual_duration)に対してかけるため、末尾フェードは
    速度変換後の尺(actual_duration / speed)を基準に計算する。
    """
    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

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

    top_bar = _build_text_bar(
        main_text, SHORTS_WIDTH, BAR_HEIGHT, TOP_FONT_SIZE, TEXT_COLOR
    )
    bottom_bar = _build_text_bar(
        sub_text, SHORTS_WIDTH, BAR_HEIGHT, BOTTOM_FONT_SIZE, SUB_TEXT_COLOR
    )
    top_bar_path = work_dir / "top_bar.png"
    bottom_bar_path = work_dir / "bottom_bar.png"
    top_bar.save(top_bar_path)
    bottom_bar.save(bottom_bar_path)

    output_duration = actual_duration / speed
    fade_start = max(0.0, output_duration - END_FADE_SECONDS)
    filter_complex = (
        f"color=c=0x{BAR_BG_COLOR[0]:02x}{BAR_BG_COLOR[1]:02x}{BAR_BG_COLOR[2]:02x}:"
        f"s={SHORTS_WIDTH}x{SHORTS_HEIGHT}[bg];"
        f"[0:v]scale={SHORTS_WIDTH}:{_SCALED_VIDEO_HEIGHT},setpts=PTS/{speed}[vid];"
        f"[bg][vid]overlay=x=0:y={BAR_HEIGHT}[bg_vid];"
        f"[bg_vid][1:v]overlay=x=0:y=0[bg_vid_top];"
        f"[bg_vid_top][2:v]overlay=x=0:y={SHORTS_HEIGHT - BAR_HEIGHT}[vout];"
        f"[0:a]atempo={speed}[a_fast];"
        # 自然な切れ目にほぼ合わせているとはいえ、確実に滑らかに終わらせるため
        # 末尾に短いフェードアウトを常にかける(速度変換後の尺を基準に計算)
        f"[a_fast]afade=t=out:st={fade_start:.3f}:d={END_FADE_SECONDS}[aout]"
    )

    _run_ffmpeg(
        [
            "-t",
            f"{actual_duration:.3f}",
            "-i",
            str(source_video_path),
            "-i",
            str(top_bar_path),
            "-i",
            str(bottom_bar_path),
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
