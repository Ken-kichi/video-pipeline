"""横長(16:9)の完成動画から、YouTubeショート(9:16)を切り出す。

以前は「動画冒頭をカットし、Canvaで縦長キャンバスの中央に貼り付け、
上下の空いたスペースに文言を入れる」という作業を手動で行っていた。
これを自動化する:
  1. 完成動画(final_video.mp4)の冒頭N秒(デフォルト60秒。script_agentが
     台本の0:00〜1:00を「単体でショートとして成立する概要パート」として
     生成する設計になっているため、これに合わせている)を切り出す
  2. 9:16の縦長キャンバスの中央に、元の16:9映像を配置する
  3. 上下の余白に、thumbnail_agentが生成するキャッチコピーを焼き込む
     (サムネイルと同じ文言を使うことで、動画・サムネイル・ショートの
     見た目に一貫性を持たせる)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
# 元動画(16:9)をSHORTS_WIDTHに合わせて縮小した高さ。エンコードの都合上偶数にする。
_SCALED_VIDEO_HEIGHT = round(SHORTS_WIDTH * 9 / 16 / 2) * 2
BAR_HEIGHT = (SHORTS_HEIGHT - _SCALED_VIDEO_HEIGHT) // 2

DEFAULT_SHORTS_DURATION_SECONDS = 60.0

_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Bold.otf"
BAR_BG_COLOR = (26, 26, 46)  # 動画本編のACCENT_COLORに近い、目に馴染むダークカラー
TEXT_COLOR = "#FFFFFF"
TEXT_OUTLINE_COLOR = "#1A1A2E"
SUB_TEXT_COLOR = "#FFE066"


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_PATH), size)


def _build_text_bar(
    text: str, width: int, height: int, font_size: int, fill: str
) -> Image.Image:
    """指定したサイズの帯に、縁取り付きの中央揃えテキストを描画する。"""
    img = Image.new("RGB", (width, height), BAR_BG_COLOR)
    draw = ImageDraw.Draw(img)
    if not text:
        return img

    font = _load_font(font_size)
    # 帯の幅に収まらない場合は自動でフォントサイズを下げる
    while font.size > 20 and draw.textlength(text, font=font) > width - 60:
        font = _load_font(font.size - 4)

    text_width = draw.textlength(text, font=font)
    x = (width - text_width) / 2
    y = (height - font.size) / 2
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=6,
        stroke_fill=TEXT_OUTLINE_COLOR,
    )
    return img


def build_shorts_video(
    source_video_path: str | Path,
    output_path: str | Path,
    main_text: str,
    sub_text: str,
    duration: float = DEFAULT_SHORTS_DURATION_SECONDS,
    work_dir: str | Path | None = None,
) -> Path:
    """完成動画の冒頭を切り出し、9:16のショート動画として書き出す。

    上段にmain_text、下段にsub_textを表示する(サムネイルと同じ役割分担)。
    """
    import subprocess

    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(work_dir) if work_dir else output_path.parent / "_shorts_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    top_bar = _build_text_bar(
        main_text, SHORTS_WIDTH, BAR_HEIGHT, font_size=64, fill=TEXT_COLOR
    )
    bottom_bar = _build_text_bar(
        sub_text, SHORTS_WIDTH, BAR_HEIGHT, font_size=48, fill=SUB_TEXT_COLOR
    )
    top_bar_path = work_dir / "top_bar.png"
    bottom_bar_path = work_dir / "bottom_bar.png"
    top_bar.save(top_bar_path)
    bottom_bar.save(bottom_bar_path)

    filter_complex = (
        f"color=c=0x{BAR_BG_COLOR[0]:02x}{BAR_BG_COLOR[1]:02x}{BAR_BG_COLOR[2]:02x}:"
        f"s={SHORTS_WIDTH}x{SHORTS_HEIGHT}[bg];"
        f"[0:v]scale={SHORTS_WIDTH}:{_SCALED_VIDEO_HEIGHT}[vid];"
        f"[bg][vid]overlay=x=0:y={BAR_HEIGHT}[bg_vid];"
        f"[bg_vid][1:v]overlay=x=0:y=0[bg_vid_top];"
        f"[bg_vid_top][2:v]overlay=x=0:y={SHORTS_HEIGHT - BAR_HEIGHT}[vout]"
    )

    command = [
        "ffmpeg",
        "-y",
        "-t",
        str(duration),
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
        "0:a:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-t",
        str(duration),
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpegの実行に失敗しました。ffmpegがインストールされているか確認してください。\n"
            f"エラー出力:\n{result.stderr[-4000:]}"
        )

    return output_path
