"""字幕の折り返し・行数計算ロジック。

video_assembler(ASS字幕の焼き込み)と、slide_image_builder(table/diagram/
code/imageスライド下部に字幕用の余白をどれだけ確保するかの計算)の両方から
参照する共通処理。焼き込まれる字幕と、余白計算に使う想定行数が別々の場所で
ズレて定義されると、長いセリフがスライド内容と重なる不具合につながるため、
折り返し幅・フォント・行数の計算方法を1箇所に集約している。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
_SUBTITLE_FONT_PATH = FONTS_DIR / "NotoSansJP-Bold.otf"

SUBTITLE_FONT_SIZE = 64
SUBTITLE_MARGIN_L = 80
SUBTITLE_MARGIN_R = 80
# ASS字幕のMarginV(画面下端から字幕ブロック下端までの余白)。video_assembler
# 側のASSスタイル生成でも同じ値を使う。
SUBTITLE_MARGIN_V = 60
VIDEO_WIDTH = 1920
# 字幕が使える横幅(px)。ここを超えたら折り返す。
SUBTITLE_MAX_WIDTH = VIDEO_WIDTH - SUBTITLE_MARGIN_L - SUBTITLE_MARGIN_R
# 字幕1行あたりの実効高さ(px)の見積もり。SUBTITLE_FONT_SIZE=64のとき、
# 行間・行送りを含めておおよそこの高さになる(libassの実際の描画結果から逆算)。
SUBTITLE_LINE_HEIGHT_PX = 100

_subtitle_measure_font: ImageFont.FreeTypeFont | None = None


def _get_subtitle_measure_font() -> ImageFont.FreeTypeFont:
    """字幕の折り返し判定に使うフォントを読み込む(実際に焼き込まれるBold体と同じもの)。"""
    global _subtitle_measure_font
    if _subtitle_measure_font is None:
        _subtitle_measure_font = ImageFont.truetype(
            str(_SUBTITLE_FONT_PATH), SUBTITLE_FONT_SIZE
        )
    return _subtitle_measure_font


def wrap_subtitle_text(text: str, max_width: int = SUBTITLE_MAX_WIDTH) -> str:
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


def count_subtitle_lines(text: str, max_width: int = SUBTITLE_MAX_WIDTH) -> int:
    """このテキストが実際に字幕として焼き込まれた際の行数を返す。

    _build_ass_subtitleが焼き込み前に行っているのと同じクリーニング
    (改行・ASSの制御文字として解釈される{}の除去)を適用してから折り返し行数を数える。
    """
    if not text:
        return 1
    cleaned = text.replace("\n", " ").replace("{", "").replace("}", "")
    wrapped = wrap_subtitle_text(cleaned, max_width)
    return wrapped.count("\\N") + 1
