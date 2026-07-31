"""スライド内容(JSON構造)からPillowで直接PNG画像を1枚ずつ生成する。

python-pptxで.pptxを組み立てる方式は、環境によっては生成物が正しく開けない
事例があった(Mac上で開けなかった)ため、PowerPointファイルを経由せず
直接画像として書き出す方式に切り替えている。DaVinci Resolveのような
動画編集ソフトにも、pptxより画像の方が素直に読み込める。

日本語フォントはOS標準フォントに依存すると環境差が出るため、
Noto Sans JP(可変フォント、OFLライセンス)をvideo_pipeline/assets/fonts/に
同梱し、どの環境でも同じ見た目になるようにしている。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Variable.ttf"

SLIDE_WIDTH = 1920
SLIDE_HEIGHT = 1080
MARGIN = 100
LINE_SPACING = 1.4

BG_COLOR = "#FFFFFF"
ACCENT_COLOR = "#4C6EF5"
TITLE_COLOR = "#1A1A2E"
BODY_COLOR = "#2B2D42"
SUBTITLE_COLOR = "#5C5F77"


def _load_font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """Noto Sans JP(可変フォント)を指定サイズ・太さで読み込む。"""
    font = ImageFont.truetype(str(FONT_PATH), size)
    try:
        font.set_variation_by_axes([weight])
    except Exception:  # noqa: BLE001 太さ調整に失敗しても通常ウェイトで続行する
        pass
    return font


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


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    fill: str,
) -> int:
    """複数行のテキストを描画し、描画後のy座標を返す。"""
    line_height = int(font.size * LINE_SPACING)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _fit_image(image_path: str, max_width: int, max_height: int) -> Image.Image:
    """アスペクト比を保ったまま指定サイズ内に収まるようリサイズする。"""
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_width, max_height), Image.LANCZOS)
    return img


def build_title_slide(title: str, subtitle: str, output_path: str | Path) -> Path:
    """表紙スライドを生成する。"""
    img = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (SLIDE_WIDTH, 24)], fill=ACCENT_COLOR)

    title_font = _load_font(76, weight=700)
    subtitle_font = _load_font(36, weight=400)

    title_lines = _wrap_text(draw, title, title_font, SLIDE_WIDTH - MARGIN * 2)
    subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, SLIDE_WIDTH - MARGIN * 2)

    title_block_height = int(title_font.size * LINE_SPACING) * len(title_lines)
    subtitle_block_height = int(subtitle_font.size * LINE_SPACING) * len(subtitle_lines)
    total_height = title_block_height + 40 + subtitle_block_height

    y = (SLIDE_HEIGHT - total_height) // 2
    y = _draw_lines(draw, title_lines, title_font, MARGIN, y, TITLE_COLOR)
    y += 40
    _draw_lines(draw, subtitle_lines, subtitle_font, MARGIN, y, SUBTITLE_COLOR)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def build_content_slide(slide_data: dict, slide_number: int, output_path: str | Path) -> Path:
    """本編の1スライドを生成する(タイトル+箇条書き、任意で右側に挿絵)。"""
    img = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (SLIDE_WIDTH, 16)], fill=ACCENT_COLOR)

    title_font = _load_font(56, weight=700)
    body_font = _load_font(38, weight=400)

    image_path = slide_data.get("image_path")
    body_max_width = SLIDE_WIDTH - MARGIN * 2
    if image_path and Path(image_path).exists():
        body_max_width = int(SLIDE_WIDTH * 0.5) - MARGIN

    # タイトル
    title_lines = _wrap_text(draw, slide_data.get("title", ""), title_font, body_max_width)
    y = 90
    y = _draw_lines(draw, title_lines, title_font, MARGIN, y, TITLE_COLOR)

    # タイトル下の区切り線
    y += 20
    draw.line([(MARGIN, y), (MARGIN + body_max_width, y)], fill=ACCENT_COLOR, width=4)
    y += 50

    # 箇条書き
    for bullet in slide_data.get("bullets", []):
        bullet_lines = _wrap_text(draw, f"・{bullet}", body_font, body_max_width)
        y = _draw_lines(draw, bullet_lines, body_font, MARGIN, y, BODY_COLOR)
        y += 16

    # 挿絵(あれば右側に配置)
    if image_path and Path(image_path).exists():
        max_img_width = int(SLIDE_WIDTH * 0.38)
        max_img_height = SLIDE_HEIGHT - 300
        thumb = _fit_image(image_path, max_img_width, max_img_height)
        img_x = SLIDE_WIDTH - MARGIN - thumb.width
        img_y = (SLIDE_HEIGHT - thumb.height) // 2 + 40
        img.paste(thumb, (img_x, img_y))

    # スライド番号
    footer_font = _load_font(24, weight=400)
    footer_text = f"{slide_number}"
    footer_width = draw.textlength(footer_text, font=footer_font)
    draw.text(
        (SLIDE_WIDTH - MARGIN - footer_width, SLIDE_HEIGHT - 60),
        footer_text,
        font=footer_font,
        fill=SUBTITLE_COLOR,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def build_slide_images(title: str, slides: list[dict], output_dir: str | Path) -> list[Path]:
    """表紙+各スライドをPNGとして書き出し、生成したファイルパスの一覧を返す。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = [build_title_slide(title, "VOICEVOX解説動画 スライド", output_dir / "slide_00_title.png")]
    for i, slide_data in enumerate(slides, start=1):
        path = build_content_slide(slide_data, i, output_dir / f"slide_{i:02d}.png")
        paths.append(path)
    return paths
