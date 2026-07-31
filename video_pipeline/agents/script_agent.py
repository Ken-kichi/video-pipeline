"""動画台本（ずんだもん×つむぎ形式）を生成・評価・修正するエージェント。"""

from video_pipeline.claude_client import call_json, call_text
from video_pipeline.config import MODEL_EVALUATE, MODEL_GENERATE
from video_pipeline.loop import run_with_evaluation_loop

LABEL = "台本エージェント"

GENERATE_SYSTEM = """あなたはVOICEVOX実況動画（ずんだもん×春日部つむぎ形式）の
台本作家です。渡されたMarkdown記事を、8〜10分程度の解説動画の台本に構成し直してください。

全体構成（重要）:
- 冒頭0:00〜1:00の1分間は「概要パート」とする。この動画で何がわかるか・見るとどんな
  メリットがあるか（例: 何を学べるか、どんな疑問が解決するか）を1分で言い切る。
  この1分間だけを切り出してもYouTubeショートとして成立するくらい、単体で完結した
  内容にすること（続きを見ないと意味が分からない引きの作り方はしない）
- 残り約9分は「詳細解説パート」とする。記事の技術的な内容を、概要パートで示した
  メリット・疑問に沿ってシーンごとに掘り下げていく

制約:
- キャラクターは「つむぎ」（解説役・標準語）と「ずんだもん」（好奇心役・語尾は「〜のだ」「〜なのだ」）の2人
- シーンごとに見出し（### シーン1：〜（開始時刻目安）のように）を付ける。
  最初のシーンは概要パート(0:00〜1:00)であることが分かる見出しにする
- 各シーンの先頭に【画面：〜】という形式で、画面に表示すべき図・表・コードを指示する
- セリフは「つむぎ「〜」」「ずんだもん「〜のだ」」の形式で1行ずつ書く
- 記事の技術的な内容（何を・なぜ・どう）を漏らさず、かつ間延びしないテンポで構成する
- 出力はMarkdown形式の台本のみ。前置きや説明文は不要
"""

EVALUATE_SYSTEM = """あなたは動画台本のレビュアーです。元記事と台本を照らし合わせ、
以下の観点で0〜100点の総合スコアと、改善が必要な点の具体的なフィードバックを返してください。

評価観点:
- 記事の重要なポイントを漏らさず網羅しているか
- ずんだもん/つむぎの口調が一貫しているか
- 8〜10分の尺に対して情報量・テンポが適切か
- 画面指示【画面：〜】が各シーンに具体的に書かれているか
- 視聴者が置いてけぼりにならない説明の順序になっているか
- 冒頭0:00〜1:00が「概要・メリット訴求」として単体で完結しており、
  YouTubeショートとして切り出しても成立する内容になっているか
- 残り約9分が、概要パートで示した内容を裏切らず詳細に掘り下げる構成になっているか

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたは動画台本の修正担当です。渡された台本を、指摘されたフィードバックに
基づいて修正してください。フィードバックで指摘されていない良い部分は変更しないこと。
出力は修正後のMarkdown台本のみ。前置きや説明文は不要です。
"""


def generate(article_text: str) -> str:
    user = f"# 元記事\n\n{article_text}\n\n上記の記事から動画台本を作成してください。"
    return call_text(GENERATE_SYSTEM, user, model=MODEL_GENERATE)


def evaluate(article_text: str, script: str) -> dict:
    user = f"# 元記事\n\n{article_text}\n\n# 台本\n\n{script}\n\n上記の台本を評価してください。"
    return call_json(EVALUATE_SYSTEM, user, model=MODEL_EVALUATE)


def revise(article_text: str, script: str, feedback: str) -> str:
    user = (
        f"# 元記事\n\n{article_text}\n\n# 現在の台本\n\n{script}\n\n"
        f"# フィードバック\n\n{feedback}\n\n上記フィードバックを反映して台本を修正してください。"
    )
    return call_text(REVISE_SYSTEM, user, model=MODEL_GENERATE)


def run(article_text: str) -> tuple[str, int, list[dict]]:
    """台本の生成→評価→修正ループを実行する。"""
    return run_with_evaluation_loop(
        label=LABEL,
        generate=lambda: generate(article_text),
        evaluate=lambda script: evaluate(article_text, script),
        revise=lambda script, feedback: revise(article_text, script, feedback),
    )
