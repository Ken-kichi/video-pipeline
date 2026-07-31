"""動画にあわせて画面に出すスライドの内容を生成・評価・修正するエージェント。

生成物はslide_image_builderでそのまま描画できるよう、layoutフィールドで
4種類のレイアウトのいずれかを指定する構造で扱う:
- bullets:     タイトル+箇条書き(従来の標準レイアウト)
- stat:        1つの数値・指標を大きく見せる(例: 0.79 -> 0.83)
- quote:       1行のキーメッセージを大きく見せる
- comparison:  2つの対象を左右に並べて比較する
"""

import json

from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_EVALUATE, MODEL_GENERATE
from video_pipeline.loop import run_with_evaluation_loop

LABEL = "スライドエージェント"

GENERATE_SYSTEM = """あなたは技術解説動画のスライド構成担当です。
元記事と動画台本をもとに、画面に表示するスライドの内容を作成してください。

NotebookLMのVideo Overviewのように、内容によってスライドのレイアウトを
変えることで単調さをなくします。各スライドについて、以下4種類の中から
最も内容に合うlayoutを1つ選んでください。

## layout: "bullets"（標準。箇条書きで説明する内容）
{"layout": "bullets", "title": "...", "bullets": ["...", "..."], "notes": "...", "image_prompt": "..."}

## layout: "stat"（1つの具体的な数値・指標が主役のスライド。例: 精度の変化）
{"layout": "stat", "title": "...", "stat_value": "0.79 -> 0.83", "stat_label": "特徴量追加後の精度変化", "notes": "..."}
- stat_valueは記事に書かれている数値をそのまま使う(創作しない)
- stat_valueは短く(20文字程度まで)。長い説明はstat_labelに書く

## layout: "quote"（1文のキーメッセージを強調したいスライド。例: まとめの核心）
{"layout": "quote", "quote_text": "データが8割、モデルが2割", "quote_context": "...", "notes": "..."}
- quote_textは短く力強い1文(20文字前後が目安)
- quote_contextは補足の一言(無ければ空文字)

## layout: "comparison"（2つの対象を対比させたいスライド。例: 汎用AI vs 自前モデル）
{"layout": "comparison", "title": "...", "left_label": "...", "left_bullets": ["...", "..."], "right_label": "...", "right_bullets": ["...", "..."], "notes": "..."}

制約:
- 台本のシーン区切り・【画面：〜】指示と対応する形でスライドを分割する
- 全体の8〜9割は"bullets"を基本としつつ、記事中で強調されている具体的な数値の
  比較には"stat"、印象的な一文には"quote"、明確な二項対立には"comparison"を使う
  （使いすぎるとかえって散漫になるため、動画全体で"stat"1〜2枚、"quote"1〜2枚、
  "comparison"1〜2枚程度を目安にする）
- 箇条書きは体言止め・短文中心にし、長い説明文をそのまま貼らない
- 図解（mermaidのフローチャートなど）や表を説明するスライドは、
  bulletsにその要点を言葉で書く（図そのものの画像は後工程で人間が挿入する前提）

image_promptについて（重要。bulletsとcomparisonのみで使用可、stat/quoteでは使わない）:
- 画像生成モデルは文字・数値を正確に描くのが苦手なので、image_promptは
  「概念を表す挿絵・比喩的なイラスト」用途に限定する
- 数値・表・コード・具体的な文字列など、正確性が必要なスライドではimage_promptを
  空文字にする（挿絵で誤魔化さず、bulletsのテキストで正確に伝える）
- 挿絵が理解の助けになる概念的なスライド（例:「汎用AIは借り物」「差別化の源泉」）
  だけ、英語で1〜2文の画像生成プロンプトを書く。プロンプト内で文字や数字を
  画像に描かせようとする指示は入れない
- stat/quoteレイアウトはテキスト自体が主役なのでimage_promptは常に空文字にする

出力はJSONのみ: {"slides": [<上記いずれかの形式のオブジェクト>, ...]}
"""

EVALUATE_SYSTEM = """あなたはスライド構成のレビュアーです。元記事・台本・スライド内容を
照らし合わせ、以下の観点で0〜100点の総合スコアとフィードバックを返してください。

評価観点:
- 台本のシーン展開とスライドの流れが一致しているか
- 各スライドの箇条書きが簡潔か（文章の丸写しになっていないか）
- 記事の重要な図・表・数値が抜け落ちていないか
- スライド枚数が多すぎ/少なすぎないか（8〜10分の動画に対して目安10〜16枚程度）
- layoutの選び方が内容に合っているか（単なる箇条書きで済む内容にstat/quoteを
  無理に使っていないか、逆に強調すべき数値やキーメッセージがbulletsに埋もれて
  いないか）。"stat"/"quote"/"comparison"を使いすぎて散漫になっていないか
- stat_valueが記事の数値と一致しているか（創作した数値になっていないか）
- image_promptが、数値・表・コードなど正確性が必要なスライドや
  stat/quoteレイアウトで空になっているか（入っていたら減点対象）

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたはスライド構成の修正担当です。フィードバックに基づいて
スライド内容を修正してください。各スライドは"layout"フィールド
("bullets"/"stat"/"quote"/"comparison"のいずれか)を持つ構造を維持してください。
出力はJSONのみ: {"slides": [<layoutに応じた形式のオブジェクト>, ...]}
"""


def _slides_to_text(slides: list[dict]) -> str:
    return json.dumps({"slides": slides}, ensure_ascii=False, indent=2)


def generate(article_text: str, script: str) -> list[dict]:
    user = (
        f"# 元記事\n\n{article_text}\n\n# 動画台本\n\n{script}\n\n"
        "上記をもとにスライド内容を作成してください。"
    )
    result = call_json(GENERATE_SYSTEM, user, model=MODEL_GENERATE)
    return result["slides"]


def evaluate(article_text: str, script: str, slides: list[dict]) -> dict:
    user = (
        f"# 元記事\n\n{article_text}\n\n# 動画台本\n\n{script}\n\n"
        f"# 現在のスライド内容\n\n{_slides_to_text(slides)}\n\n上記を評価してください。"
    )
    return call_json(EVALUATE_SYSTEM, user, model=MODEL_EVALUATE)


def revise(script: str, slides: list[dict], feedback: str) -> list[dict]:
    user = (
        f"# 動画台本\n\n{script}\n\n# 現在のスライド内容\n\n{_slides_to_text(slides)}\n\n"
        f"# フィードバック\n\n{feedback}\n\n上記フィードバックを反映してスライド内容を修正してください。"
    )
    result = call_json(REVISE_SYSTEM, user, model=MODEL_GENERATE)
    return result["slides"]


def run(article_text: str, script: str) -> tuple[list[dict], int, list[dict]]:
    """スライド内容の生成→評価→修正ループを実行する。"""
    return run_with_evaluation_loop(
        label=LABEL,
        generate=lambda: generate(article_text, script),
        evaluate=lambda slides: evaluate(article_text, script, slides),
        revise=lambda slides, feedback: revise(script, slides, feedback),
    )
