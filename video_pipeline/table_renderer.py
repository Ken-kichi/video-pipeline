"""記事中のMarkdownの表をPillowで画像として描画する。

code_renderer/diagram_rendererと同じく、生成したPNGをslide_image_builderの
"table"レイアウトがそのまま貼り付ける想定。フォントはスライド本体と
同じNoto Sans JPを使い、見た目のトーンを合わせる。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Variable.ttf"

TABLE_FONT_SIZE = 32
TABLE_LINE_SPACING = 1.3
HEADER_BG_COLOR = "#4C6EF5"
HEADER_TEXT_COLOR = "#FFFFFF"
ROW_BG_COLOR_EVEN = "#FFFFFF"
ROW_BG_COLOR_ODD = "#EEF1FD"
BORDER_COLOR = "#C4CFFB"
BODY_TEXT_COLOR = "#2B2D42"
CELL_PADDING_X = 24
CELL_PADDING_Y = 18
MIN_COL_WIDTH = 200
MAX_COL_WIDTH = 900


def _load_font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_FONT_PATH), size)
    try:
        font.set_variation_by_axes([weight])
    except Exception:  # noqa: BLE001 太さ調整に失敗しても通常ウェイトで続行する
        pass
    return font


def _wrap_cell_text(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        trial = current + ch
        if current and font.getlength(trial) > max_width:
            lines.append(current)
            current = ch
        else:
            current = trial
    lines.append(current)
    return lines


def _column_widths(
    all_rows: list[list[str]],
    header_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
    target_total_width: int,
    padding_x: int = CELL_PADDING_X,
    stretch: bool = True,
) -> list[int]:
    """列内容に応じた比率で、指定した合計幅に収まる列幅を決める。

    列数が多い表では、MIN_COL_WIDTHをそのまま全列の下限にすると
    (col_count * MIN_COL_WIDTH > target_total_widthとなり)合計が
    target_total_widthを超えてしまい、この関数の「指定した合計幅に収める」
    という契約に違反してしまう。実効的な下限をtarget_total_width/col_count
    まで下げることでこれを防ぐ(結果、極端に列数が多い表では個々の列幅が
    MIN_COL_WIDTHを下回ることがあるが、合計がtarget_total_widthを超えない
    ことを優先する)。
    """
    col_count = len(all_rows[0])
    effective_min_width = min(float(MIN_COL_WIDTH), target_total_width / col_count)
    preferred = [effective_min_width] * col_count
    for row_index, row in enumerate(all_rows):
        font = header_font if row_index == 0 else body_font
        for col_index in range(col_count):
            cell = row[col_index] if col_index < len(row) else ""
            width = font.getlength(str(cell)) + padding_x * 2
            preferred[col_index] = max(
                preferred[col_index], min(width, MAX_COL_WIDTH)
            )

    total_preferred = sum(preferred)
    if not stretch and total_preferred <= target_total_width:
        # 内容が狭い表を無理に引き伸ばさず、自然な幅のまま返す
        return [max(1, int(w)) for w in preferred]
    scale = target_total_width / total_preferred
    return [max(1, int(w * scale)) for w in preferred]


def render_table_pil(
    header: list[str],
    rows: list[list[str]],
    font_size: int = TABLE_FONT_SIZE,
    max_width: int = 1600,
    stretch: bool = True,
) -> Image.Image:
    """表の見出し行・データ行を、指定フォントサイズでPIL画像として描画する。

    余白・行間はfont_sizeに比例させる(TABLE_FONT_SIZE基準の値を拡縮)。
    stretch=Falseなら、内容が狭い表をmax_widthまで引き伸ばさない。
    """
    header_font = _load_font(font_size, weight=700)
    body_font = _load_font(font_size, weight=400)
    line_height = int(font_size * TABLE_LINE_SPACING)
    padding_x = max(8, round(font_size * CELL_PADDING_X / TABLE_FONT_SIZE))
    padding_y = max(6, round(font_size * CELL_PADDING_Y / TABLE_FONT_SIZE))

    all_rows = [header] + rows
    col_widths = _column_widths(
        all_rows, header_font, body_font, max_width, padding_x, stretch
    )
    total_width = sum(col_widths)

    wrapped_rows: list[list[list[str]]] = []
    row_heights: list[int] = []
    for row_index, row in enumerate(all_rows):
        font = header_font if row_index == 0 else body_font
        wrapped_cells: list[list[str]] = []
        max_lines = 1
        for col_index, col_width in enumerate(col_widths):
            cell = row[col_index] if col_index < len(row) else ""
            cell_lines = _wrap_cell_text(str(cell), font, col_width - padding_x * 2)
            wrapped_cells.append(cell_lines)
            max_lines = max(max_lines, len(cell_lines))
        wrapped_rows.append(wrapped_cells)
        row_heights.append(max_lines * line_height + padding_y * 2)

    total_height = sum(row_heights)
    img = Image.new("RGB", (total_width, total_height), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    y = 0
    for row_index, wrapped_cells in enumerate(wrapped_rows):
        row_height = row_heights[row_index]
        is_header = row_index == 0
        bg_color = (
            HEADER_BG_COLOR
            if is_header
            else (ROW_BG_COLOR_ODD if row_index % 2 == 0 else ROW_BG_COLOR_EVEN)
        )
        text_color = HEADER_TEXT_COLOR if is_header else BODY_TEXT_COLOR
        font = header_font if is_header else body_font

        x = 0
        for col_index, cell_lines in enumerate(wrapped_cells):
            col_width = col_widths[col_index]
            draw.rectangle(
                [(x, y), (x + col_width, y + row_height)],
                fill=bg_color,
                outline=BORDER_COLOR,
                width=1,
            )
            text_y = y + padding_y
            for line in cell_lines:
                draw.text((x + padding_x, text_y), line, font=font, fill=text_color)
                text_y += line_height
            x += col_width
        y += row_height

    return img


def fit_table_pil(
    header: list[str],
    rows: list[list[str]],
    max_width: int,
    max_height: int,
    max_font_size: int = 44,
    min_font_size: int = 22,
) -> Image.Image:
    """max_width x max_heightの枠に収まる範囲で、最も大きいフォントで表を描画する。

    以前は固定のフォントサイズで広い画像に描画してから枠に合わせて縮小して
    いたため、行数の多い表では実効のフォントが20px前後まで小さくなり、動画で
    読めなかった。ここでは最終的な表示サイズに合わせて、大きいフォントから順に
    描画し直し、枠の高さに収まる最大のフォントサイズを選ぶ。最小フォントでも
    収まらない場合のみ、最後に縮小して枠へ収める。
    """
    img = None
    for font_size in range(max_font_size, min_font_size - 1, -2):
        img = render_table_pil(header, rows, font_size, max_width, stretch=False)
        if img.height <= max_height and img.width <= max_width:
            return img
    assert img is not None
    scale = min(max_width / img.width, max_height / img.height, 1.0)
    return img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.LANCZOS,
    )


def render_table_image(
    header: list[str],
    rows: list[list[str]],
    output_path: str | Path,
    max_width: int = 1600,
) -> Path:
    """表の見出し行・データ行をPNG画像として書き出す。"""
    img = render_table_pil(header, rows, TABLE_FONT_SIZE, max_width)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path
