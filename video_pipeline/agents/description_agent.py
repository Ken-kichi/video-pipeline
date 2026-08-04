"""台本をもとにYouTube概要欄のテキストを生成・評価・修正するエージェント。

最終的に保存されるdescription.txtには以下を含める:
- YouTubeタイトル（LLM生成ではなく決定的に先頭へ付け足す。video_title
  ＝スライドのタイトルと同じ値なので、アップロード時にタイトル欄へそのまま
  コピーできる。pipeline.py側で付け足す）
- 動画の概要文（何がわかるか・見るメリット）
- タイムスタンプ付きの目次（YouTubeのチャプター機能に対応する形式）
- （任意）使用技術・ライブラリ
- 元記事(Zenn/note等)のURLを貼る行
- 関連ハッシュタグ
- VOICEVOX/立ち絵のクレジット（LLM生成ではなく決定的に付け足す。利用規約上
  必須の表記なので、LLMに書かせて表現を変えられたり抜け落ちたりしないようにする）
"""

from video_pipeline.claude_client import call_text, call_json
from video_pipeline.config import (
    MODEL_EVALUATE,
    MODEL_GENERATE,
    TSUMUGI_ILLUSTRATOR_CREDIT,
    ZUNDAMON_ILLUSTRATOR_CREDIT,
)
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
3. 空行を挟んで「🛠 使用技術」という見出し（台本・記事に登場する主要な
   ライブラリ・技術の名前を箇条書きで。例: Python、scikit-learn、pandas）
   - URLは書かない（実在しないURLを創作するリスクを避けるため、名前だけ書く。
     リンクを付けたい場合は人間が後から追記する前提）
   - 台本・記事に技術要素がほとんど無い場合はこのブロックを省略してよい
4. 空行を挟んで「元記事」という見出しと、渡された元記事URLをそのまま1行で記載
5. 空行を挟んで関連ハッシュタグを3〜5個（#機械学習 のような形式）

制約:
- 元記事URLが "（URL未設定）" のようなプレースホルダーの場合は、それをそのまま記載する
  （実在しないURLを創作しない）
- 出力は上記ブロックのプレーンテキストのみ。前置きや説明文、コードフェンスは不要
  （VOICEVOXやイラストのクレジット表記は書かなくてよい。別途決定的に付け足すため）
"""

EVALUATE_SYSTEM = """あなたはYouTube概要欄のレビュアーです。動画台本と概要欄テキストを
照らし合わせ、以下の観点で0〜100点の総合スコアとフィードバックを返してください。

評価観点:
- 概要文が動画の価値（何がわかるか・メリット）を簡潔に伝えているか
- 台本冒頭0:00〜1:00の概要パートの内容と矛盾していないか
- 目次の1行目が 0:00 から始まっているか
- 目次の時刻・順序が台本のシーン展開と一致しているか
- 使用技術セクションに実在しないURLが創作されていないか（無くて良い、
  あるなら名前だけであるべき）
- 元記事URLを貼る行が存在するか（プレースホルダーのままでも構わない）
- ハッシュタグが動画の内容に関連しているか

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたは概要欄の修正担当です。フィードバックに基づいて概要欄テキストを
修正してください。出力は概要文・目次・(使用技術)・元記事URL・ハッシュタグを
含むプレーンテキストのみ（VOICEVOX/イラストのクレジットは書かなくてよい）。
"""


def build_credits_block(
    tsumugi_credit: str = TSUMUGI_ILLUSTRATOR_CREDIT,
    zundamon_credit: str = ZUNDAMON_ILLUSTRATOR_CREDIT,
) -> str:
    """VOICEVOX・立ち絵イラストのクレジット表記を決定的に生成する。

    VOICEVOXの音声そのものは規約で決まった表記(「VOICEVOX:キャラ名」)を使う。
    立ち絵イラストのクレジットはPSDファイルからは判別できないため、
    config.TSUMUGI_ILLUSTRATOR_CREDIT等で明示的に設定してもらう必要がある。
    未設定の場合はプレースホルダーを入れる(投稿前に必ず差し替えること)。
    """
    tsumugi_illust = (
        tsumugi_credit or "（つむぎ立ち絵のイラストクレジットをここに入れてください）"
    )
    zundamon_illust = (
        zundamon_credit
        or "（ずんだもん立ち絵のイラストクレジットをここに入れてください）"
    )

    return (
        "【クレジット】\n"
        "VOICEVOX:春日部つむぎ\n"
        "VOICEVOX:ずんだもん\n"
        f"つむぎ立ち絵: {tsumugi_illust}\n"
        f"ずんだもん立ち絵: {zundamon_illust}"
    )


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
