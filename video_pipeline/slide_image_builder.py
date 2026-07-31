"""スライド内容(JSON構造)からPillowで直接PNG画像を1枚ずつ生成する。

python-pptxで.pptxを組み立てる方式は、環境によっては生成物が正しく開けない
事例があった(Mac上で開けなかった)ため、PowerPointファイルを経由せず
直接画像として書き出す方式に切り替えている。DaVinci Resolveのような
動画編集ソフトにも、pptxより画像の方が素直に読み込める。

日本語フォントはOS標準フォントに依存すると環境差が出るため、
Noto Sans JP(可変フォント、OFLライセンス)をvideo_pipeline/assets/fonts/に
同梱し、どの環境でも同じ見た目になるようにしている。

内容によってレイアウトを変える(NotebookLMのVideo Overviewを参考に):
- bullets:    タイトル+箇条書き(標準)
- stat:       1つの数値・指標を大きく見せる
- quote:      1行のキーメッセージを大きく見せる
- comparison: 2つの対象を左右に並べて比較する
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Variable.ttf"

SLIDE_WIDTH = 1920
SLIDE_HEIGHT = 1080
MARGIN = 100
LINE_SPACING = 1.4

BG_COLOR = "#FFFFFF"
QUOTE_BG_COLOR = "#EEF1FD"
ACCENT_COLOR = "#4C6EF5"
ACCENT_COLOR_SOFT = "#C4CFFB"
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
    """複数行のテキストを左揃えで描画し、描画後のy座標を返す。"""
    line_height = int(font.size * LINE_SPACING)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _draw_lines_centered(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    center_x: int,
    y: int,
    fill: str,
) -> int:
    """複数行のテキストを中央揃えで描画し、描画後のy座標を返す。"""
    line_height = int(font.size * LINE_SPACING)
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text((center_x - width / 2, y), line, font=font, fill=fill)
        y += line_height
    return y


def _block_height(lines: list[str], font: ImageFont.FreeTypeFont) -> int:
    return int(font.size * LINE_SPACING) * len(lines)


def _fit_image(image_path: str, max_width: int, max_height: int) -> Image.Image:
    """アスペクト比を保ったまま指定サイズ内に収まるようリサイズする。"""
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_width, max_height), Image.LANCZOS)
    return img


def _draw_footer(draw: ImageDraw.ImageDraw, slide_number: int) -> None:
    footer_font = _load_font(24, weight=400)
    footer_text = f"{slide_number}"
    footer_width = draw.textlength(footer_text, font=footer_font)
    draw.text(
        (SLIDE_WIDTH - MARGIN - footer_width, SLIDE_HEIGHT - 60),
        footer_text,
        font=footer_font,
        fill=SUBTITLE_COLOR,
    )


def build_title_slide(title: str, subtitle: str, output_path: str | Path) -> Path:
    """表紙スライドを生成する。"""
    img = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (SLIDE_WIDTH, 24)], fill=ACCENT_COLOR)

    title_font = _load_font(76, weight=700)
    subtitle_font = _load_font(36, weight=400)

    title_lines = _wrap_text(draw, title, title_font, SLIDE_WIDTH - MARGIN * 2)
    subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, SLIDE_WIDTH - MARGIN * 2)

    total_height = (
        _block_height(title_lines, title_font) + 40 + _block_height(subtitle_lines, subtitle_font)
    )

    y = (SLIDE_HEIGHT - total_height) // 2
    y = _draw_lines(draw, title_lines, title_font, MARGIN, y, TITLE_COLOR)
    y += 40
    _draw_lines(draw, subtitle_lines, subtitle_font, MARGIN, y, SUBTITLE_COLOR)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def _render_bullets(img: Image.Image, draw: ImageDraw.ImageDraw, slide_data: dict) -> None:
    """タイトル+箇条書き(標準レイアウト)。任意で右側に挿絵を配置する。"""
    title_font = _load_font(56, weight=700)
    body_font = _load_font(38, weight=400)

    image_path = slide_data.get("image_path")
    body_max_width = SLIDE_WIDTH - MARGIN * 2
    if image_path and Path(image_path).exists():
        body_max_width = int(SLIDE_WIDTH * 0.5) - MARGIN

    title_lines = _wrap_text(draw, slide_data.get("title", ""), title_font, body_max_width)
    y = 90
    y = _draw_lines(draw, title_lines, title_font, MARGIN, y, TITLE_COLOR)

    y += 20
    draw.line([(MARGIN, y), (MARGIN + body_max_width, y)], fill=ACCENT_COLOR, width=4)
    y += 50

    for bullet in slide_data.get("bullets", []):
        bullet_lines = _wrap_text(draw, f"・{bullet}", body_font, body_max_width)
        y = _draw_lines(draw, bullet_lines, body_font, MARGIN, y, BODY_COLOR)
        y += 16

    if image_path and Path(image_path).exists():
        max_img_width = int(SLIDE_WIDTH * 0.38)
        max_img_height = SLIDE_HEIGHT - 300
        thumb = _fit_image(image_path, max_img_width, max_img_height)
        img_x = SLIDE_WIDTH - MARGIN - thumb.width
        img_y = (SLIDE_HEIGHT - thumb.height) // 2 + 40
        img.paste(thumb, (img_x, img_y))


def _render_stat(img: Image.Image, draw: ImageDraw.ImageDraw, slide_data: dict) -> None:
    """1つの数値・指標を画面中央に大きく見せるレイアウト。"""
    title = slide_data.get("title", "")
    stat_value = slide_data.get("stat_value", "")
    stat_label = slide_data.get("stat_label", "")

    if title:
        title_font = _load_font(40, weight=700)
        title_lines = _wrap_text(draw, title, title_font, SLIDE_WIDTH - MARGIN * 2)
        _draw_lines_centered(draw, title_lines, title_font, SLIDE_WIDTH // 2, 110, SUBTITLE_COLOR)

    # 数値の背景に淡いアクセント帯を敷く
    band_top = SLIDE_HEIGHT // 2 - 160
    band_bottom = SLIDE_HEIGHT // 2 + 160
    draw.rectangle([(0, band_top), (SLIDE_WIDTH, band_bottom)], fill=QUOTE_BG_COLOR)

    stat_font = _load_font(140, weight=700)
    stat_lines = _wrap_text(draw, stat_value, stat_font, SLIDE_WIDTH - MARGIN * 2)
    stat_height = _block_height(stat_lines, stat_font)
    y = (band_top + band_bottom) // 2 - stat_height // 2
    _draw_lines_centered(draw, stat_lines, stat_font, SLIDE_WIDTH // 2, y, ACCENT_COLOR)

    if stat_label:
        label_font = _load_font(34, weight=400)
        label_lines = _wrap_text(draw, stat_label, label_font, SLIDE_WIDTH - MARGIN * 2)
        _draw_lines_centered(draw, label_lines, label_font, SLIDE_WIDTH // 2, band_bottom + 50, BODY_COLOR)


def _render_quote(img: Image.Image, draw: ImageDraw.ImageDraw, slide_data: dict) -> None:
    """1文のキーメッセージを画面中央に大きく見せるレイアウト。"""
    # 全面に淡いアクセント背景を敷いて他のスライドと視覚的に区別する
    draw.rectangle([(0, 0), (SLIDE_WIDTH, SLIDE_HEIGHT)], fill=QUOTE_BG_COLOR)
    draw.rectangle([(0, 0), (SLIDE_WIDTH, 24)], fill=ACCENT_COLOR)

    quote_text = slide_data.get("quote_text", "")
    quote_context = slide_data.get("quote_context", "")

    # 装飾用の大きな引用符
    mark_font = _load_font(220, weight=700)
    draw.text((MARGIN - 20, 60), "“", font=mark_font, fill=ACCENT_COLOR_SOFT)

    quote_font = _load_font(88, weight=700)
    quote_lines = _wrap_text(draw, quote_text, quote_font, SLIDE_WIDTH - MARGIN * 2)
    context_font = _load_font(32, weight=400)
    context_lines = _wrap_text(draw, quote_context, context_font, SLIDE_WIDTH - MARGIN * 2) if quote_context else []

    total_height = _block_height(quote_lines, quote_font)
    if context_lines:
        total_height += 40 + _block_height(context_lines, context_font)

    y = (SLIDE_HEIGHT - total_height) // 2
    y = _draw_lines_centered(draw, quote_lines, quote_font, SLIDE_WIDTH // 2, y, TITLE_COLOR)
    if context_lines:
        y += 40
        _draw_lines_centered(draw, context_lines, context_font, SLIDE_WIDTH // 2, y, SUBTITLE_COLOR)


def _render_comparison(img: Image.Image, draw: ImageDraw.ImageDraw, slide_data: dict) -> None:
    """2つの対象を左右に並べて比較するレイアウト。"""
    title_font = _load_font(56, weight=700)
    label_font = _load_font(40, weight=700)
    body_font = _load_font(34, weight=400)

    title_lines = _wrap_text(draw, slide_data.get("title", ""), title_font, SLIDE_WIDTH - MARGIN * 2)
    y_title = 90
    y_after_title = _draw_lines(draw, title_lines, title_font, MARGIN, y_title, TITLE_COLOR)
    y_after_title += 20
    draw.line(
        [(MARGIN, y_after_title), (SLIDE_WIDTH - MARGIN, y_after_title)], fill=ACCENT_COLOR, width=4
    )

    content_top = y_after_title + 60
    column_width = (SLIDE_WIDTH - MARGIN * 2 - 80) // 2
    left_x = MARGIN
    right_x = MARGIN + column_width + 80
    divider_x = SLIDE_WIDTH // 2

    # 中央の区切り線
    draw.line([(divider_x, content_top), (divider_x, SLIDE_HEIGHT - 120)], fill=ACCENT_COLOR_SOFT, width=3)

    for x, label_key, bullets_key in (
        (left_x, "left_label", "left_bullets"),
        (right_x, "right_label", "right_bullets"),
    ):
        y = content_top
        label_lines = _wrap_text(draw, slide_data.get(label_key, ""), label_font, column_width)
        y = _draw_lines(draw, label_lines, label_font, x, y, ACCENT_COLOR)
        y += 20
        for bullet in slide_data.get(bullets_key, []):
            bullet_lines = _wrap_text(draw, f"・{bullet}", body_font, column_width)
            y = _draw_lines(draw, bullet_lines, body_font, x, y, BODY_COLOR)
            y += 14


_LAYOUT_RENDERERS = {
    "bullets": _render_bullets,
    "stat": _render_stat,
    "quote": _render_quote,
    "comparison": _render_comparison,
}


def build_content_slide(slide_data: dict, slide_number: int, output_path: str | Path) -> Path:
    """本編の1スライドを生成する。layoutフィールドに応じて描画方法を切り替える。"""
    img = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    layout = slide_data.get("layout", "bullets")
    if layout != "quote":
        # quoteレイアウトは背景ごと専用の色を敷くため、ここでは共通のアクセント帯のみ
        draw.rectangle([(0, 0), (SLIDE_WIDTH, 16)], fill=ACCENT_COLOR)

    renderer = _LAYOUT_RENDERERS.get(layout, _render_bullets)
    renderer(img, draw, slide_data)

    _draw_footer(draw, slide_number)

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
