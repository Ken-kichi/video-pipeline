"""動画にあわせて画面に出すPowerPointのスライド内容を生成・評価・修正するエージェント。

生成物はpython-pptxでそのまま組み立てられるよう、
[{"title": str, "bullets": [str, ...], "notes": str}, ...] という構造で扱う。
"""

import json

from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_EVALUATE, MODEL_GENERATE
from video_pipeline.loop import run_with_evaluation_loop

LABEL = "スライドエージェント"

GENERATE_SYSTEM = """あなたは技術解説動画のスライド構成担当です。
元記事と動画台本をもとに、画面に表示するスライドの内容を作成してください。

制約:
- 台本のシーン区切り・【画面：〜】指示と対応する形でスライドを分割する
- 1スライドにつき title（短い見出し）、bullets（3〜5個程度の簡潔な箇条書き）、
  notes（台本のどのシーンに対応するかのメモ）、image_prompt（後述）を作る
- 箇条書きは体言止め・短文中心にし、長い説明文をそのまま貼らない
- 図解（mermaidのフローチャートなど）や表・具体的な数値を説明するスライドは、
  bulletsにその要点を言葉で書く（図そのものの画像は後工程で人間が挿入する前提）

image_promptについて（重要）:
- 画像生成モデルは文字・数値を正確に描くのが苦手なので、image_promptは
  「概念を表す挿絵・比喩的なイラスト」用途に限定する
- 数値・表・コード・具体的な文字列など、正確性が必要なスライドではimage_promptを
  空文字にする（挿絵で誤魔化さず、bulletsのテキストで正確に伝える）
- 挿絵が理解の助けになる概念的なスライド（例:「汎用AIは借り物」「差別化の源泉」）
  だけ、英語で1〜2文の画像生成プロンプトを書く。プロンプト内で文字や数字を
  画像に描かせようとする指示は入れない

出力はJSONのみ:
{"slides": [{"title": "...", "bullets": ["...", "..."], "notes": "...", "image_prompt": "..."}, ...]}
"""

EVALUATE_SYSTEM = """あなたはスライド構成のレビュアーです。元記事・台本・スライド内容を
照らし合わせ、以下の観点で0〜100点の総合スコアとフィードバックを返してください。

評価観点:
- 台本のシーン展開とスライドの流れが一致しているか
- 各スライドの箇条書きが簡潔か（文章の丸写しになっていないか）
- 記事の重要な図・表・数値が抜け落ちていないか
- スライド枚数が多すぎ/少なすぎないか（8〜10分の動画に対して目安10〜16枚程度）
- image_promptが、数値・表・コードなど正確性が必要なスライドで空になっているか
  （そうしたスライドにimage_promptが入っていたら減点対象）

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたはスライド構成の修正担当です。フィードバックに基づいて
スライド内容を修正してください。出力はJSONのみ:
{"slides": [{"title": "...", "bullets": ["...", "..."], "notes": "...", "image_prompt": "..."}, ...]}
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
