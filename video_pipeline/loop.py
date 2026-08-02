"""生成→評価→修正のループを共通化したヘルパー。

台本エージェント・スライドエージェント・VOICEVOXテキストエージェントは
中身の生成ロジックこそ違うが、ループの回し方は同じなのでここに集約する。
"""

from collections.abc import Callable
from typing import TypeVar

from video_pipeline.config import MAX_REVISION_LOOPS, SCORE_THRESHOLD

T = TypeVar("T")


def run_with_evaluation_loop(
    label: str,
    generate: Callable[[], T],
    evaluate: Callable[[T], dict],
    revise: Callable[[T, str], T],
) -> tuple[T, int, list[dict]]:
    """generate→evaluate→(必要なら)reviseを最大MAX_REVISION_LOOPS回繰り返す。

    Args:
        label: ログ表示用のラベル（例: "台本エージェント"）
        generate: 引数なしで初回コンテンツを生成する関数
        evaluate: コンテンツを受け取り {"score": int, "feedback": str} を返す関数
        revise: (コンテンツ, フィードバック) を受け取り修正済みコンテンツを返す関数

    Returns:
        (最も評価の高かったコンテンツ, そのスコア, 各試行の履歴)
    """
    history: list[dict] = []
    output = generate()
    best_output, best_score = output, -1

    for attempt in range(1, MAX_REVISION_LOOPS + 1):
        result = evaluate(output)
        score = int(result.get("score", 0))
        feedback = str(result.get("feedback", ""))
        history.append({"attempt": attempt, "score": score, "feedback": feedback})
        print(f"  [{label}] {attempt}回目の評価スコア: {score}点")

        if score > best_score:
            best_output, best_score = output, score

        if score >= SCORE_THRESHOLD:
            break
        if attempt == MAX_REVISION_LOOPS:
            print(
                f"  [{label}] ループ上限({MAX_REVISION_LOOPS}回)に到達。最高スコア案を採用します。"
            )
            break

        output = revise(output, feedback)

    return best_output, best_score, history
