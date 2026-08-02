"""動画のサムネイルに載せる文言と、図解イラストの材料を生成する。

サムネイル画像に大きく表示できる文字数はごく少ない(1〜2行、20文字程度まで)
ため、他のエージェントのような生成→評価→修正のループは行わず、
1回の生成呼び出しのみで済ませる軽量な設計にしている。

main_text/sub_textだけをGeminiに渡すと、単に「文字+汎用的な背景」の
サムネイルにしかならない(実際にそうなった)。良いサムネイルは、比較構造や
具体的な数値をアイコン・パネルなどの図解で見せている場合が多いため、
visual_summaryとして動画の核心的な内容を要約し、Geminiが自律的に
図解をデザインするための材料として渡す。
"""

from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_GENERATE

LABEL = "サムネイルエージェント"

GENERATE_SYSTEM = """あなたはYouTubeサムネイルの企画担当です。
動画台本をもとに、サムネイルに使う文言と、Gemini画像生成モデルが図解イラストを
デザインするための材料を作成してください。

出力する3項目:
- main_text: サムネイルに大きく載せるキャッチコピー。1行、10〜16文字程度
  (長いと画像内で読みにくくなる)。クリックしたくなる、驚き・疑問・具体的な
  数字を使った表現にする（例:「汎用AIだけじゃダメ？」）
- sub_text: 補足の1行。無ければ空文字でよい(合わせても20文字程度まで)
- visual_summary: この動画の核心的な内容を2〜3文で要約したもの。
  Geminiがこれを読んで比較図・アイコン・矢印などの図解イラストを
  自律的にデザインするための材料になるので、以下を必ず含める:
  - 対比構造があれば両方の対象を明示する（例:「汎用AIは借り物のモデルで
    差別化しにくい。一方、自前モデルは模倣されにくい資産になる」）
  - 具体的な数値・キーワードがあれば含める（例:「精度は0.79から0.83に改善」）
  - 単なる感想ではなく、図解の材料になる具体的な内容にする

制約:
- 誇張・釣りタイトルにはしない(台本の内容と矛盾しない範囲にする)
- 台本冒頭0:00〜1:00の概要パートの内容を踏まえる

出力はJSONのみ: {"main_text": "...", "sub_text": "...", "visual_summary": "..."}
"""


def generate(script: str) -> dict:
    """台本からサムネイル用の文言と図解材料(visual_summary)を生成する。"""
    user = (
        f"# 動画台本\n\n{script}\n\n"
        "上記からサムネイル用の文言と図解材料(visual_summary)を作成してください。"
    )
    result = call_json(GENERATE_SYSTEM, user, model=MODEL_GENERATE)
    return {
        "main_text": result.get("main_text", ""),
        "sub_text": result.get("sub_text", ""),
        "visual_summary": result.get("visual_summary", ""),
    }
