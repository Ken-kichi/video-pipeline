"""字幕の折り返し・行数計算ロジック。

video_assembler(ASS字幕の焼き込み)と、slide_image_builder(table/diagram/
code/imageスライド下部に字幕用の余白をどれだけ確保するかの計算)の両方から
参照する共通処理。焼き込まれる字幕と、余白計算に使う想定行数が別々の場所で
ズレて定義されると、長いセリフがスライド内容と重なる不具合につながるため、
折り返し幅・フォント・行数の計算方法を1箇所に集約している。
"""

import re
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
# 1つの字幕イベントに表示する最大行数。これを超える長いセリフは、複数の字幕に
# 分けて順番に表示する(split_subtitle_chunks参照)。長いセリフを1つの字幕として
# 一度に出すと、5〜6行(500px以上)の字幕がスライドの図や表を覆い、スライド側も
# その最悪ケースに合わせて図を縮小するため、図が小さくなりすぎる不具合があった。
SUBTITLE_MAX_LINES = 2

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


_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?])")
_CLAUSE_END_RE = re.compile(r"(?<=[、，,])")


def _clean_subtitle_text(text: str) -> str:
    """字幕として焼き込む前のクリーニング(改行・ASS制御文字{}の除去)。"""
    return text.replace("\n", " ").replace("{", "").replace("}", "")


def _wrapped_line_count(text: str, max_width: int) -> int:
    return wrap_subtitle_text(text, max_width).count("\\N") + 1


def _hard_split(text: str, max_lines: int, max_width: int) -> list[str]:
    """句読点で分けても収まらない長い塊を、折り返し行数の単位で機械的に分割する。"""
    wrapped_lines = wrap_subtitle_text(text, max_width).split("\\N")
    return [
        "".join(wrapped_lines[i : i + max_lines])
        for i in range(0, len(wrapped_lines), max_lines)
    ]


def split_subtitle_chunks(
    text: str,
    max_lines: int = SUBTITLE_MAX_LINES,
    max_width: int = SUBTITLE_MAX_WIDTH,
) -> list[str]:
    """長いセリフを、max_lines行に収まる字幕の列に分割する。

    文末(。！？)で区切り、1文がmax_lines行を超える場合は読点(、)で、それでも
    収まらなければ折り返し行の単位で分割する。区切った塊は、max_lines行に収まる
    範囲でなるべくまとめて詰める(字幕の切り替わりを必要以上に増やさない)。
    """
    cleaned = _clean_subtitle_text(text).strip()
    if not cleaned:
        return [text]

    units: list[str] = []
    for sentence in filter(None, _SENTENCE_END_RE.split(cleaned)):
        if _wrapped_line_count(sentence, max_width) <= max_lines:
            units.append(sentence)
            continue
        for clause in filter(None, _CLAUSE_END_RE.split(sentence)):
            if _wrapped_line_count(clause, max_width) <= max_lines:
                units.append(clause)
            else:
                units.extend(_hard_split(clause, max_lines, max_width))

    chunks: list[str] = []
    current = ""
    for unit in units:
        trial = current + unit
        if current and _wrapped_line_count(trial, max_width) > max_lines:
            chunks.append(current)
            current = unit
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks


def count_subtitle_lines(text: str, max_width: int = SUBTITLE_MAX_WIDTH) -> int:
    """このテキストが実際に字幕として焼き込まれた際の最大行数を返す。

    長いセリフはsplit_subtitle_chunksで複数の字幕に分けて表示されるため、
    同時に画面に出る行数は分割後の各字幕の最大値(通常はSUBTITLE_MAX_LINES以下)。
    """
    if not text:
        return 1
    return max(
        _wrapped_line_count(chunk, max_width)
        for chunk in split_subtitle_chunks(text, max_width=max_width)
    )
