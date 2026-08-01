"""動画にあわせて画面に出すスライドの内容を生成・評価・修正するエージェント。

生成物はslide_image_builderでそのまま描画できるよう、layoutフィールドで
4種類のレイアウトのいずれかを指定する構造で扱う:
- bullets:     タイトル+箇条書き(従来の標準レイアウト)
- stat:        1つの数値・指標を大きく見せる(例: 0.79 -> 0.83)
- quote:       1行のキーメッセージを大きく見せる
- comparison:  2つの対象を左右に並べて比較する

どのレイアウトにも background_prompt（背景に敷く抽象イラストの生成プロンプト、
文字・数字は含めない）と scene_number（台本のシーン番号。動画組み立て時に
音声とスライドを対応させるための機械可読な値）を持たせる。実際のテキストは
背景の上にPillowで正確に描画するため、背景画像に数値やコードの正確性を
求める必要はない。
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
{"layout": "bullets", "scene_number": 1, "title": "...", "bullets": ["...", "..."], "notes": "...", "background_prompt": "..."}

## layout: "stat"（1つの具体的な数値・指標が主役のスライド。例: 精度の変化）
{"layout": "stat", "scene_number": 1, "title": "...", "stat_value": "0.79 -> 0.83", "stat_label": "特徴量追加後の精度変化", "notes": "...", "background_prompt": "..."}
- stat_valueは記事に書かれている数値をそのまま使う(創作しない)
- stat_valueは短く(20文字程度まで)。長い説明はstat_labelに書く

## layout: "quote"（1文のキーメッセージを強調したいスライド。例: まとめの核心）
{"layout": "quote", "scene_number": 1, "quote_text": "データが8割、モデルが2割", "quote_context": "...", "notes": "...", "background_prompt": "..."}
- quote_textは短く力強い1文(20文字前後が目安)
- quote_contextは補足の一言(無ければ空文字)

## layout: "comparison"（2つの対象を対比させたいスライド。例: 汎用AI vs 自前モデル）
{"layout": "comparison", "scene_number": 1, "title": "...", "left_label": "...", "left_bullets": ["...", "..."], "right_label": "...", "right_bullets": ["...", "..."], "notes": "...", "background_prompt": "..."}

scene_numberについて（重要）:
- 台本の見出し「### シーン<N>：〜」の<N>の数字をそのまま入れる
  （動画組み立て時に音声とスライドを対応させるために使う機械可読な値なので、
  必ず台本のシーン番号と一致させる。自由な説明はnotesに書く）
- 1つのシーンに複数のスライドを割り当てても構わない（同じscene_numberを
  複数のスライドに使ってよい）が、シーンを飛ばしたり存在しない番号を
  使ったりしないこと

制約:
- 台本のシーン区切り・【画面：〜】指示と対応する形でスライドを分割する
- 全体の8〜9割は"bullets"を基本としつつ、記事中で強調されている具体的な数値の
  比較には"stat"、印象的な一文には"quote"、明確な二項対立には"comparison"を使う
  （使いすぎるとかえって散漫になるため、動画全体で"stat"1〜2枚、"quote"1〜2枚、
  "comparison"1〜2枚程度を目安にする）
- 箇条書きは体言止め・短文中心にし、長い説明文をそのまま貼らない
- 図解（mermaidのフローチャートなど）や表を説明するスライドは、
  bulletsにその要点を言葉で書く（図そのものの画像は後工程で人間が挿入する前提）

background_promptについて（重要。全レイアウト共通、ほぼ全スライドで書く）:
- これはスライド全体の背景に敷く抽象的なイラストの生成プロンプト。
  この上に文字を重ねて描画するので、背景画像自体に文字・数字・記号を
  描かせる指示は絶対に入れない（画像生成モデルは文字を正確に描けないため。
  実際の数値や用語はテキストとして別途正確に描画される）
- スライドの内容を象徴する情景・比喩を英語で1文で書く
  （例: "a small seedling growing into a tree, representing data accumulation"）
- 色使いやアートスタイルの指定は不要（後工程で全スライド共通のスタイルに
  統一するため）。情景・構図の内容だけを書く
- 適切な情景が思いつかない場合や、抽象的すぎて意味を持たない場合のみ
  空文字にする（背景無しの単色になる）

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
- scene_numberが台本の実際のシーン番号と一致しているか（欠番・範囲外が
  無いか。ここがズレると動画組み立て時に音声とスライドが対応しなくなる）
- background_promptに文字・数字を描かせる指示が紛れ込んでいないか
  （紛れ込んでいたら減点対象）
- background_promptがスライド内容と無関係になっていないか

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたはスライド構成の修正担当です。フィードバックに基づいて
スライド内容を修正してください。各スライドは"layout"フィールド
("bullets"/"stat"/"quote"/"comparison"のいずれか)と、台本のシーン番号と
一致した"scene_number"(整数)を持つ構造を維持してください。
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
