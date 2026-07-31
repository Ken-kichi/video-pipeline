"""記事1本から台本・スライド・VOICEVOX用テキスト・概要欄を生成する一連の流れ。

流れ:
  1. 台本エージェント: 記事 -> 台本（生成→評価→修正ループ）
  2. スライドエージェント: 記事+台本 -> スライド内容（生成→評価→修正ループ）
  3. VOICEVOXテキストエージェント: 台本 -> 読み上げ用テキスト（生成→評価→修正ループ）
  4. 総合エージェント: 3つの整合性を採点し、90点未満なら該当箇所を修正して再採点
     （他の3エージェントと同じくSCORE_THRESHOLD/MAX_REVISION_LOOPSに従う）
  5. 概要欄エージェント: 確定した台本 -> 概要文・目次・元記事リンク・ハッシュタグ
     （生成→評価→修正ループ）
  6. 台本(.md)・VOICEVOXテキスト(.txt)・スライド(.pptx)・概要欄(.txt)をファイルに保存する
"""

from pathlib import Path

from video_pipeline.agents import (
    description_agent,
    integration_agent,
    script_agent,
    slides_agent,
    voicevox_agent,
)
from video_pipeline.config import MAX_REVISION_LOOPS, SCORE_THRESHOLD
from video_pipeline.io_utils import read_markdown, write_text_file
from video_pipeline.pptx_builder import build_pptx

DEFAULT_ARTICLE_URL_PLACEHOLDER = "（ここに元記事のURLを貼ってください）"


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


def run_pipeline(
    article_path: str,
    output_dir: str = "output",
    video_title: str = "解説動画",
    article_url: str | None = None,
) -> dict:
    article_text = read_markdown(article_path)
    article_url = article_url or DEFAULT_ARTICLE_URL_PLACEHOLDER

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

    output_dir_path = Path(output_dir)
    script_path = write_text_file(output_dir_path / "script.md", script)
    voicevox_path = write_text_file(output_dir_path / "voicevox_script.txt", voicevox_text)
    description_path = write_text_file(output_dir_path / "description.txt", description)
    pptx_path = build_pptx(video_title, slides, output_dir_path / "slides.pptx")

    print("\n=== 完了 ===")
    print(
        f"台本スコア: {script_score}点 / "
        f"スライドスコア: {slides_score}点 / "
        f"VOICEVOXテキストスコア: {voicevox_score}点 / "
        f"整合性スコア: {integration_score}点 / "
        f"概要欄スコア: {description_score}点"
    )
    print(f"台本        : {script_path}")
    print(f"VOICEVOXテキスト: {voicevox_path}")
    print(f"スライド     : {pptx_path}")
    print(f"概要欄       : {description_path}")

    return {
        "script_path": script_path,
        "voicevox_path": voicevox_path,
        "pptx_path": pptx_path,
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
