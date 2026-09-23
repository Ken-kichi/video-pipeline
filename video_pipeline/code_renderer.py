"""コードブロックをシンタックスハイライト付きの画像として描画する。

pygmentsでトークンごとの色を決め、Pillowで直接ピクセルに描画する
(HTMLやSVG経由ではなく、スライド画像と同じPillow描画パイプラインに
乗せるため)。フォントはスライドと同じNoto Sans JPではなく、
等幅フォントが必要なため別途DejaVu Sans Mono相当を使う。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token

# コード表示に使う等幅フォント。日本語コメントが含まれる可能性もあるため、
# 等幅フォントに日本語グリフが無い文字はNoto Sans JPで補う簡易フォールバックにする。
_MONO_FONT_PATH = (
    Path(__file__).parent / "assets" / "fonts" / "JetBrainsMono-Regular.ttf"
)
_JP_FALLBACK_FONT_PATH = (
    Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Regular.otf"
)

CODE_FONT_SIZE = 30
CODE_LINE_SPACING = 1.35
CODE_BG_COLOR = "#1E1E2E"
CODE_PADDING = 40

# pygmentsのトークン種別 -> 表示色(ダーク背景に映える配色)
TOKEN_COLORS = {
    Token.Keyword: "#FF7AD1",
    Token.Keyword.Constant: "#FF7AD1",
    Token.Name.Builtin: "#82C7FF",
    Token.Name.Function: "#82C7FF",
    Token.Name.Class: "#82C7FF",
    Token.Literal.String: "#B5F4A5",
    Token.Literal.Number: "#FFD479",
    Token.Comment: "#8892B0",
    Token.Operator: "#F5F5F5",
    Token.Punctuation: "#F5F5F5",
}
DEFAULT_TOKEN_COLOR = "#F5F5F5"


def _token_color(token_type) -> str:
    for candidate, color in TOKEN_COLORS.items():
        if token_type in candidate:
            return color
    return DEFAULT_TOKEN_COLOR


def _load_mono_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_MONO_FONT_PATH), size)


def _load_jp_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_JP_FALLBACK_FONT_PATH), size)


def _char_font(
    ch: str, mono_font: ImageFont.FreeTypeFont, jp_font: ImageFont.FreeTypeFont
) -> ImageFont.FreeTypeFont:
    """半角文字は等幅フォント、日本語などは同サイズのNoto Sans JPで描く。"""
    return mono_font if ord(ch) < 0x3000 else jp_font


def render_code_image(
    code: str,
    language: str,
    output_path: str | Path,
    max_width: int = 1500,
) -> Path:
    """コード文字列をシンタックスハイライト付きのPNG画像として書き出す。"""
    try:
        lexer = get_lexer_by_name(language)
    except Exception:  # noqa: BLE001 未知の言語名でも装飾無しで表示を継続する
        lexer = get_lexer_by_name("text")

    mono_font = _load_mono_font(CODE_FONT_SIZE)
    jp_font = _load_jp_font(CODE_FONT_SIZE)
    line_height = int(CODE_FONT_SIZE * CODE_LINE_SPACING)
    max_text_width = max_width - CODE_PADDING * 2

    # 幅計測用の使い捨てdraw(実際の描画キャンバスとは別。フォントの計測は
    # 画像サイズに依存しないため、これで折り返し後の行数を先に確定できる)。
    measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    lines = code.rstrip("\n").split("\n")

    # 各論理行をpygmentsでトークン化し、キャンバス幅を超える手前で複数の
    # 描画行(visual row)に折り返す。以前はmax_widthに対する幅チェックが
    # 無く、長い行はPillowの暗黙のクリッピングでキャンバス外に黙って
    # 切り捨てられ、画面から見えなくなっていた。
    visual_rows: list[list[tuple[str, str, ImageFont.FreeTypeFont]]] = []
    for line in lines:
        current_row: list[tuple[str, str, ImageFont.FreeTypeFont]] = []
        current_width = 0.0
        for token_type, value in lex(line, lexer):
            color = _token_color(token_type)
            for ch in value:
                if ch == "\n":
                    continue
                font = _char_font(ch, mono_font, jp_font)
                ch_width = measure_draw.textlength(ch, font=font)
                if current_row and current_width + ch_width > max_text_width:
                    visual_rows.append(current_row)
                    current_row = []
                    current_width = 0.0
                current_row.append((ch, color, font))
                current_width += ch_width
        visual_rows.append(current_row)  # 空行も1描画行として残す

    height = CODE_PADDING * 2 + line_height * max(1, len(visual_rows))
    img = Image.new("RGB", (max_width, height), CODE_BG_COLOR)
    draw = ImageDraw.Draw(img)

    x_start, y = CODE_PADDING, CODE_PADDING
    for row in visual_rows:
        x = x_start
        for ch, color, font in row:
            draw.text((x, y), ch, font=font, fill=color)
            x += draw.textlength(ch, font=font)
        y += line_height

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path
