"""記事1本から台本・スライド画像・VOICEVOX用テキスト・概要欄を生成する一連の流れ。

流れ:
  1. 台本エージェント: 記事 -> 台本（生成→評価→修正ループ）
  2. スライドエージェント: 記事+台本 -> スライド内容（生成→評価→修正ループ）
     ※各スライドには任意でbackground_prompt（背景用、文字・数値は描かせない）が付く
  3. VOICEVOXテキストエージェント: 台本 -> 読み上げ用テキスト（生成→評価→修正ループ）
  4. 総合エージェント: 3つの整合性を採点し、90点未満なら該当箇所を修正して再採点
     （他の3エージェントと同じくSCORE_THRESHOLD/MAX_REVISION_LOOPSに従う）
  5. 概要欄エージェント: 確定した台本 -> 概要文・目次・元記事リンク・ハッシュタグ
     （生成→評価→修正ループ）
  6. (任意) background_promptが設定されているスライドについて、Gemini(Nano Banana系)で
     背景画像を生成する（GENERATE_SLIDE_IMAGES有効時のみ。失敗しても処理は継続する）
     背景の上にはスクリム(半透明パネル)を敷いた上でテキストをPillowで正確に描画するため、
     背景画像の絵柄に関わらず数値・専門用語の正確性は保たれる
  7. 台本(.md)・VOICEVOXテキスト(.txt)・スライド画像(.png)・概要欄(.txt)を保存する
     （.pptxは環境によって開けない事例があったため廃止し、直接PNGを書き出す）
"""

from datetime import datetime
from pathlib import Path

from video_pipeline.agents import (
    description_agent,
    integration_agent,
    script_agent,
    slides_agent,
    voicevox_agent,
)
from video_pipeline.config import GENERATE_SLIDE_IMAGES, MAX_REVISION_LOOPS, SCORE_THRESHOLD
from video_pipeline.image_generator import generate_slide_background
from video_pipeline.io_utils import extract_h1_title, read_markdown, write_text_file
from video_pipeline.slide_image_builder import build_slide_images

DEFAULT_ARTICLE_URL_PLACEHOLDER = "（ここに元記事のURLを貼ってください）"
DEFAULT_VIDEO_TITLE = "解説動画"


def _run_integration_loop(
    article_text: str, script: str, slides: list[dict], voicevox_text: str
) -> tuple[str, list[dict], str, int, list[dict]]:
    """総合エージェントによる整合性チェック→(必要なら)修正ループ。

    generate/evaluate/reviseが同一の1つの成果物に閉じているloop.pyの
    run_with_evaluation_loopとは違い、ここでは「3つの成果物のうち
    フィードバックがついたものだけをそれぞれの担当エージェントに直させる」
    という分岐が必要なため、専用のループとして実装している。
    """
    history: list[dict] = []
    best = (script, slides, voicevox_text)
    best_score = -1

    for attempt in range(1, MAX_REVISION_LOOPS + 1):
        result = integration_agent.check(script, slides, voicevox_text)
        score = int(result.get("score", 0))
        issues = result.get("issues", [])
        history.append({"attempt": attempt, "score": score, "issues": issues})

        print(f"  [{integration_agent.LABEL}] {attempt}回目の整合性スコア: {score}点")
        for issue in issues:
            print(f"   - {issue}")

        if score > best_score:
            best_score = score
            best = (script, slides, voicevox_text)

        if score >= SCORE_THRESHOLD:
            break
        if attempt == MAX_REVISION_LOOPS:
            print(
                f"  [{integration_agent.LABEL}] "
                f"ループ上限({MAX_REVISION_LOOPS}回)に到達。最高スコア案を採用します。"
            )
            break

        script_feedback = result.get("script_feedback", "")
        slides_feedback = result.get("slides_feedback", "")
        voicevox_feedback = result.get("voicevox_feedback", "")

        if script_feedback:
            print("  台本を修正中...")
            script = script_agent.revise(article_text, script, script_feedback)
        if slides_feedback:
            print("  スライドを修正中...")
            slides = slides_agent.revise(script, slides, slides_feedback)
        if voicevox_feedback:
            print("  VOICEVOXテキストを修正中...")
            voicevox_text = voicevox_agent.revise(script, voicevox_text, voicevox_feedback)

    script, slides, voicevox_text = best
    return script, slides, voicevox_text, best_score, history


def _generate_slide_backgrounds(slides: list[dict], backgrounds_dir: Path) -> list[dict]:
    """background_promptが設定されているスライドについて背景画像を生成し、
    background_pathを追加する。

    生成に失敗したスライドはbackground_pathを付けずそのまま返す
    (slide_image_builder側で単色背景にフォールバックする)。
    """
    for i, slide_data in enumerate(slides):
        prompt = slide_data.get("background_prompt", "")
        if not prompt:
            continue
        print(f"  スライド{i + 1}の背景を生成中: {prompt[:40]}...")
        background_path = generate_slide_background(prompt, backgrounds_dir / f"slide_{i + 1}.png")
        if background_path:
            slide_data["background_path"] = str(background_path)
    return slides


def run_pipeline(
    article_path: str,
    output_dir: str = "output",
    video_title: str | None = None,
    article_url: str | None = None,
    generate_images: bool | None = None,
) -> dict:
    article_text = read_markdown(article_path)
    article_url = article_url or DEFAULT_ARTICLE_URL_PLACEHOLDER
    generate_images = GENERATE_SLIDE_IMAGES if generate_images is None else generate_images

    if video_title is None:
        video_title = extract_h1_title(article_text) or DEFAULT_VIDEO_TITLE
        print(f"タイトル未指定のため記事の見出し1から自動設定: 「{video_title}」")

    print("=== 台本エージェント ===")
    script, script_score, _ = script_agent.run(article_text)

    print("=== スライドエージェント ===")
    slides, slides_score, _ = slides_agent.run(article_text, script)

    print("=== VOICEVOXテキストエージェント ===")
    voicevox_text, voicevox_score, _ = voicevox_agent.run(script)

    print("=== 総合エージェント（整合性チェック） ===")
    script, slides, voicevox_text, integration_score, integration_history = _run_integration_loop(
        article_text, script, slides, voicevox_text
    )

    print("=== 概要欄エージェント ===")
    description, description_score, _ = description_agent.run(script, article_url)

    output_dir_path = Path(output_dir) / datetime.now().strftime("%Y%m%d_%H%M%S")

    if generate_images:
        print("=== スライド背景の生成（Gemini） ===")
        slides = _generate_slide_backgrounds(slides, output_dir_path / "backgrounds")

    script_path = write_text_file(output_dir_path / "script.md", script)
    voicevox_path = write_text_file(output_dir_path / "voicevox_script.txt", voicevox_text)
    description_path = write_text_file(output_dir_path / "description.txt", description)
    slide_image_paths = build_slide_images(video_title, slides, output_dir_path / "slides")

    print("\n=== 完了 ===")
    print(f"出力先ディレクトリ: {output_dir_path}")
    print(
        f"台本スコア: {script_score}点 / "
        f"スライドスコア: {slides_score}点 / "
        f"VOICEVOXテキストスコア: {voicevox_score}点 / "
        f"整合性スコア: {integration_score}点 / "
        f"概要欄スコア: {description_score}点"
    )
    print(f"台本        : {script_path}")
    print(f"VOICEVOXテキスト: {voicevox_path}")
    print(f"スライド画像 : {output_dir_path / 'slides'}/ ({len(slide_image_paths)}枚)")
    print(f"概要欄       : {description_path}")

    return {
        "output_dir": output_dir_path,
        "script_path": script_path,
        "voicevox_path": voicevox_path,
        "slide_image_paths": slide_image_paths,
        "description_path": description_path,
        "scores": {
            "script": script_score,
            "slides": slides_score,
            "voicevox": voicevox_score,
            "integration": integration_score,
            "description": description_score,
        },
        "integration_history": integration_history,
    }
