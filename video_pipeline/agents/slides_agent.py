"""動画にあわせて画面に出すスライドの内容を生成・評価・修正するエージェント。

生成物はslide_image_builderでそのまま描画できるよう、layoutフィールドで
6種類のレイアウトのいずれかを指定する構造で扱う:
- bullets:     タイトル+箇条書き(従来の標準レイアウト)
- stat:        1つの数値・指標を大きく見せる(例: 0.79 -> 0.83)
- quote:       1行のキーメッセージを大きく見せる
- comparison:  2つの対象を左右に並べて比較する
- code:        記事中の実際のコードブロックをシンタックスハイライト付きで表示
- diagram:     記事中の実際のmermaid図を画像として表示

どのレイアウトにも background_prompt（背景に敷く抽象イラストの生成プロンプト、
文字・数字は含めない）と scene_number（台本のシーン番号。動画組み立て時に
音声とスライドを対応させるための機械可読な値）を持たせる。実際のテキストは
背景の上にPillowで正確に描画するため、背景画像に数値やコードの正確性を
求める必要はない。code/diagramはbackground_promptを使わない(記事そのものの
コード/図が主役のため)。
"""

import json

from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_EVALUATE, MODEL_GENERATE
from video_pipeline.loop import run_with_evaluation_loop

LABEL = "スライドエージェント"

GENERATE_SYSTEM = """あなたは技術解説動画のスライド構成担当です。
元記事と動画台本をもとに、画面に表示するスライドの内容を作成してください。

NotebookLMのVideo Overviewのように、内容によってスライドのレイアウトを
変えることで単調さをなくします。各スライドについて、以下6種類の中から
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

## layout: "code"（台本のセリフで記事中の具体的なコードに言及しているスライド）
{"layout": "code", "scene_number": 1, "title": "...", "code_ref": 0, "caption": "...", "notes": "..."}
- code_refは、渡された「記事中のコードブロック一覧」に書かれているcode_refの
  番号をそのまま使う(創作禁止。存在しない番号を使わない)
- captionはそのコードの要点を一言で(無ければ空文字)。background_promptは使わない

## layout: "diagram"（台本のセリフで記事中の具体的な図(mermaid)に言及しているスライド）
{"layout": "diagram", "scene_number": 1, "title": "...", "diagram_ref": 0, "caption": "...", "notes": "..."}
- diagram_refは、渡された「記事中のmermaid図一覧」に書かれているdiagram_refの
  番号をそのまま使う(創作禁止。存在しない番号を使わない)
- captionはその図の要点を一言で(無ければ空文字)。background_promptは使わない

scene_numberについて（重要）:
- 台本の見出し「### シーン<N>：〜」の<N>の数字をそのまま入れる
  （動画組み立て時に音声とスライドを対応させるために使う機械可読な値なので、
  必ず台本のシーン番号と一致させる。自由な説明はnotesに書く）
- 1つのシーンに複数のスライドを割り当てても構わない（同じscene_numberを
  複数のスライドに使ってよい）が、シーンを飛ばしたり存在しない番号を
  使ったりしないこと

制約:
- 台本のシーン区切り・【画面：〜】指示と対応する形でスライドを分割する
- 【重要・必須】台本冒頭0:00〜1:00の概要パートは、この動画から切り出してYouTube
  ショートとして単体公開されるため画面の停滞が離脱に直結する。概要パートに含まれる
  シーンには必ず1シーン1枚以上のスライドを割り当て、概要パート全体を通して同じ
  スライドを2シーン以上にわたって使い回さないこと（概要パートのシーンが4〜6個ある
  想定なので、概要パートだけで最低4〜6枚のスライドになる計算になる）
- 【重要・必須】"bullets"だけで構成すると単調になり視聴者が離脱するため、
  以下を必ず満たすこと（"目安"ではなく必須条件）:
  - "stat"レイアウトを最低1枚は使う。台本中に具体的な数値の変化・比較
    （精度の改善、before/afterの数字など）が必ず1つ以上あるはずなので、
    それを担当するスライドは"bullets"ではなく"stat"にする
  - "quote"レイアウトを最低1枚は使う。台本のまとめや、最も強調したい
    一文（キャッチーな言い回し）を担当するスライドは"bullets"ではなく
    "quote"にする
  - "comparison"レイアウトを最低1枚は使う。台本中で2つの対象を
    対比している箇所（AとBの比較、旧と新、メリットとデメリットなど）を
    担当するスライドは"bullets"ではなく"comparison"にする
  - 【重要】台本のセリフが記事中の具体的なコードに言及しているシーンでは
    "bullets"で説明を書くのではなく、渡された「記事中のコードブロック一覧」
    から該当するcode_refを選んで"code"レイアウトにする
  - 【重要】台本のセリフが記事中の具体的なmermaid図に言及しているシーンでは
    同様に「記事中のmermaid図一覧」から該当するdiagram_refを選んで
    "diagram"レイアウトにする
  - コードブロック/mermaid図が渡されていない場合や、台本がどれにも
    具体的に言及していない場合は、code/diagramレイアウトを使わなくてよい
    (存在しないcode_ref/diagram_refを創作するより、bulletsにする方が良い)
  - stat/quote/comparisonは合計して全体の1〜3割程度、残りをbullets/code/diagramに
    する（動画全体でstat/quote/comparisonが1枚も無いのは失格。使いすぎて
    散漫になるのも避ける）
- 箇条書きは体言止め・短文中心にし、長い説明文をそのまま貼らない
- 図解（mermaidのフローチャートなど）や表を説明するスライドで、対応する
  mermaid図が渡されていない場合は、bulletsにその要点を言葉で書く

background_promptについて（重要。bullets/stat/quote/comparisonで使用。
code/diagramでは使わない）:
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
- 【重要】概要パート(0:00〜1:00)に含まれる各シーンに、それぞれ専用のスライドが
  割り当てられているか。概要パートのシーンが1枚のスライドを共有している箇所が
  あれば必ず減点し、どのシーンに新規スライドを追加すべきか指摘する
  （このパートはショート動画として単体で使われるため、画面が長時間変わらない
  ことは離脱の主要因になる）
- 【重要】"stat"・"quote"・"comparison"それぞれが最低1枚以上使われているか。
  1枚も無いレイアウトがあれば必ず大きく減点し、台本のどの部分をそのレイアウトに
  すべきか具体的にフィードバックに書く（例:「シーン10の0.79→0.83の箇所をstatに」）
- 【重要】台本のセリフが記事中の具体的なコード/mermaid図に言及しているのに、
  対応する"code"/"diagram"レイアウトを使わず"bullets"で済ませているスライドが
  無いか。あれば減点し、該当するcode_ref/diagram_refを指摘する
- code_ref/diagram_refが、渡された一覧に実在する番号か（存在しない番号を
  創作していたら大きく減点する）
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
("bullets"/"stat"/"quote"/"comparison"/"code"/"diagram"のいずれか)と、
台本のシーン番号と一致した"scene_number"(整数)を持つ構造を維持してください。
code_ref/diagram_refは渡された一覧に実在する番号だけを使ってください。
出力はJSONのみ: {"slides": [<layoutに応じた形式のオブジェクト>, ...]}
"""


def _slides_to_text(slides: list[dict]) -> str:
    return json.dumps({"slides": slides}, ensure_ascii=False, indent=2)


def generate(article_text: str, script: str, asset_summary: str = "") -> list[dict]:
    asset_block = f"\n\n{asset_summary}\n" if asset_summary else ""
    user = (
        f"# 元記事\n\n{article_text}\n\n# 動画台本\n\n{script}\n{asset_block}\n"
        "上記をもとにスライド内容を作成してください。"
    )
    result = call_json(GENERATE_SYSTEM, user, model=MODEL_GENERATE)
    return result["slides"]


def evaluate(
    article_text: str, script: str, slides: list[dict], asset_summary: str = ""
) -> dict:
    asset_block = f"\n\n{asset_summary}\n" if asset_summary else ""
    user = (
        f"# 元記事\n\n{article_text}\n\n# 動画台本\n\n{script}\n{asset_block}\n"
        f"# 現在のスライド内容\n\n{_slides_to_text(slides)}\n\n上記を評価してください。"
    )
    return call_json(EVALUATE_SYSTEM, user, model=MODEL_EVALUATE)


def revise(
    script: str, slides: list[dict], feedback: str, asset_summary: str = ""
) -> list[dict]:
    asset_block = f"\n\n{asset_summary}\n" if asset_summary else ""
    user = (
        f"# 動画台本\n\n{script}\n{asset_block}\n# 現在のスライド内容\n\n{_slides_to_text(slides)}\n\n"
        f"# フィードバック\n\n{feedback}\n\n上記フィードバックを反映してスライド内容を修正してください。"
    )
    result = call_json(REVISE_SYSTEM, user, model=MODEL_GENERATE)
    return result["slides"]


def run(
    article_text: str, script: str, asset_summary: str = ""
) -> tuple[list[dict], int, list[dict]]:
    """スライド内容の生成→評価→修正ループを実行する。

    asset_summaryはarticle_assets.summarize_for_prompt()で作った、記事中の
    コードブロック/mermaid図の一覧(あれば)。無ければ空文字のままでよく、
    その場合はcode/diagramレイアウトは使われない。
    """
    return run_with_evaluation_loop(
        label=LABEL,
        generate=lambda: generate(article_text, script, asset_summary),
        evaluate=lambda slides: evaluate(article_text, script, slides, asset_summary),
        revise=lambda slides, feedback: revise(script, slides, feedback, asset_summary),
    )
