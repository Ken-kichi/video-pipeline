"""スライド内容の構造データ(list[dict])から実際の.pptxファイルを組み立てる。

デザインの作り込みは行わず、タイトル+箇条書き+スピーカーノートという
シンプルな構成にしている。見た目の調整は人間がPowerPoint/Resolve側の
作業で行う前提。
"""

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt

TITLE_LAYOUT_INDEX = 0  # タイトルスライド
CONTENT_LAYOUT_INDEX = 1  # タイトル+コンテンツ


def build_pptx(title: str, slides: list[dict], output_path: str | Path) -> Path:
    """slidesの各要素 {"title", "bullets", "notes"} からpptxを生成する。"""
    prs = Presentation()

    # 表紙
    title_slide = prs.slides.add_slide(prs.slide_layouts[TITLE_LAYOUT_INDEX])
    title_slide.shapes.title.text = title
    if len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = "VOICEVOX解説動画 スライド"

    # 各コンテンツスライド
    for slide_data in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[CONTENT_LAYOUT_INDEX])
        slide.shapes.title.text = slide_data.get("title", "")

        body = slide.placeholders[1]
        text_frame = body.text_frame
        text_frame.clear()

        bullets = slide_data.get("bullets", [])
        for i, bullet in enumerate(bullets):
            paragraph = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
            paragraph.text = bullet
            paragraph.level = 0
            paragraph.font.size = Pt(22)

        notes = slide_data.get("notes", "")
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return output_path
