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
) -> list[int]:
    """列内容に応じた比率で、指定した合計幅に収まる列幅を決める。"""
    col_count = len(all_rows[0])
    preferred = [float(MIN_COL_WIDTH)] * col_count
    for row_index, row in enumerate(all_rows):
        font = header_font if row_index == 0 else body_font
        for col_index in range(col_count):
            cell = row[col_index] if col_index < len(row) else ""
            width = font.getlength(str(cell)) + CELL_PADDING_X * 2
            preferred[col_index] = max(
                preferred[col_index], min(width, MAX_COL_WIDTH)
            )

    total_preferred = sum(preferred)
    scale = target_total_width / total_preferred
    return [max(MIN_COL_WIDTH, int(w * scale)) for w in preferred]


def render_table_image(
    header: list[str],
    rows: list[list[str]],
    output_path: str | Path,
    max_width: int = 1600,
) -> Path:
    """表の見出し行・データ行をPNG画像として書き出す。"""
    header_font = _load_font(TABLE_FONT_SIZE, weight=700)
    body_font = _load_font(TABLE_FONT_SIZE, weight=400)
    line_height = int(TABLE_FONT_SIZE * TABLE_LINE_SPACING)

    all_rows = [header] + rows
    col_widths = _column_widths(all_rows, header_font, body_font, max_width)
    total_width = sum(col_widths)

    wrapped_rows: list[list[list[str]]] = []
    row_heights: list[int] = []
    for row_index, row in enumerate(all_rows):
        font = header_font if row_index == 0 else body_font
        wrapped_cells: list[list[str]] = []
        max_lines = 1
        for col_index, col_width in enumerate(col_widths):
            cell = row[col_index] if col_index < len(row) else ""
            cell_lines = _wrap_cell_text(
                str(cell), font, col_width - CELL_PADDING_X * 2
            )
            wrapped_cells.append(cell_lines)
            max_lines = max(max_lines, len(cell_lines))
        wrapped_rows.append(wrapped_cells)
        row_heights.append(max_lines * line_height + CELL_PADDING_Y * 2)

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
            text_y = y + CELL_PADDING_Y
            for line in cell_lines:
                draw.text((x + CELL_PADDING_X, text_y), line, font=font, fill=text_color)
                text_y += line_height
            x += col_width
        y += row_height

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path
