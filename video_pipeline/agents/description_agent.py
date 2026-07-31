"""台本をもとにYouTube概要欄のテキストを生成・評価・修正するエージェント。

概要欄には以下を含める:
- 動画の概要文（何がわかるか・見るメリット）
- タイムスタンプ付きの目次（YouTubeのチャプター機能に対応する形式）
- 元記事(Zenn/note等)のURLを貼る行
- 関連ハッシュタグ
"""

from video_pipeline.claude_client import call_text, call_json
from video_pipeline.config import MODEL_EVALUATE, MODEL_GENERATE
from video_pipeline.loop import run_with_evaluation_loop

LABEL = "概要欄エージェント"

GENERATE_SYSTEM = """あなたはYouTube動画の概要欄作成担当です。渡された動画台本をもとに、
視聴者が最後まで見たくなるような概要欄のテキストを作成してください。

概要欄の構成（この順番・見出しで作成する）:
1. 概要文（3〜5行程度）
   - この動画で何がわかるか、見るとどんなメリットがあるかを簡潔に
   - 台本冒頭0:00〜1:00の「概要パート」の内容と矛盾しないようにする
2. 空行を挟んで「【目次】」という見出し
   - 台本の各シーン見出しに書かれている開始時刻をそのまま使う
   - 1行目は必ず "0:00 概要" のように 0:00 から始める（YouTubeのチャプター機能の要件）
   - 各行は "M:SS タイトル" または "MM:SS タイトル" の形式（台本の見出しをそのまま
     使うのではなく、視聴者に伝わりやすい短いタイトルに言い換える）
3. 空行を挟んで「元記事」という見出しと、渡された元記事URLをそのまま1行で記載
4. 空行を挟んで関連ハッシュタグを3〜5個（#機械学習 のような形式）

制約:
- 元記事URLが "（URL未設定）" のようなプレースホルダーの場合は、それをそのまま記載する
  （実在しないURLを創作しない）
- 出力は上記4ブロックのプレーンテキストのみ。前置きや説明文、コードフェンスは不要
"""

EVALUATE_SYSTEM = """あなたはYouTube概要欄のレビュアーです。動画台本と概要欄テキストを
照らし合わせ、以下の観点で0〜100点の総合スコアとフィードバックを返してください。

評価観点:
- 概要文が動画の価値（何がわかるか・メリット）を簡潔に伝えているか
- 台本冒頭0:00〜1:00の概要パートの内容と矛盾していないか
- 目次の1行目が 0:00 から始まっているか
- 目次の時刻・順序が台本のシーン展開と一致しているか
- 元記事URLを貼る行が存在するか（プレースホルダーのままでも構わない）
- ハッシュタグが動画の内容に関連しているか

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたは概要欄の修正担当です。フィードバックに基づいて概要欄テキストを
修正してください。出力は概要文・目次・元記事URL・ハッシュタグを含むプレーンテキストのみ。
"""


def generate(script: str, article_url: str) -> str:
    user = (
        f"# 動画台本\n\n{script}\n\n# 元記事URL\n\n{article_url}\n\n"
        "上記から動画概要欄のテキストを作成してください。"
    )
    return call_text(GENERATE_SYSTEM, user, model=MODEL_GENERATE)


def evaluate(script: str, description: str) -> dict:
    user = (
        f"# 動画台本\n\n{script}\n\n# 現在の概要欄テキスト\n\n{description}\n\n"
        "上記を評価してください。"
    )
    return call_json(EVALUATE_SYSTEM, user, model=MODEL_EVALUATE)


def revise(script: str, description: str, feedback: str) -> str:
    user = (
        f"# 動画台本\n\n{script}\n\n# 現在の概要欄テキスト\n\n{description}\n\n"
        f"# フィードバック\n\n{feedback}\n\n上記フィードバックを反映して修正してください。"
    )
    return call_text(REVISE_SYSTEM, user, model=MODEL_GENERATE)


def run(script: str, article_url: str) -> tuple[str, int, list[dict]]:
    """概要欄テキストの生成→評価→修正ループを実行する。"""
    return run_with_evaluation_loop(
        label=LABEL,
        generate=lambda: generate(script, article_url),
        evaluate=lambda description: evaluate(script, description),
        revise=lambda description, feedback: revise(script, description, feedback),
    )
