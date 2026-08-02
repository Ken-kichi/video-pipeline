"""動画のサムネイルに載せる、短く目を引くキャッチコピーを生成する。

サムネイル画像に大きく表示できる文字数はごく少ない(1〜2行、20文字程度まで)
ため、他のエージェントのような生成→評価→修正のループは行わず、
1回の生成呼び出しのみで済ませる軽量な設計にしている。
"""

from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_GENERATE

LABEL = "サムネイルエージェント"

GENERATE_SYSTEM = """あなたはYouTubeサムネイルのコピーライターです。
動画台本をもとに、サムネイル画像に大きく載せる短いキャッチコピーを作成してください。

制約:
- main_textは1行、10〜16文字程度(長いと画像内で読みにくくなる)
- sub_textは補足の1行、無ければ空文字にしてよい(合わせても20文字程度まで)
- クリックしたくなる、驚き・疑問・具体的な数字を使った表現にする
  （例:「汎用AIだけじゃダメ？」「精度0.79→0.83」のような具体性・意外性）
- 誇張・釣りタイトルにはしない(台本の内容と矛盾しない範囲にする)
- 台本冒頭0:00〜1:00の概要パートの内容を踏まえる

出力はJSONのみ: {"main_text": "...", "sub_text": "..."}
"""


def generate(script: str) -> dict:
    """台本からサムネイル用のmain_text/sub_textを生成する。"""
    user = f"# 動画台本\n\n{script}\n\n上記からサムネイル用のキャッチコピーを作成してください。"
    result = call_json(GENERATE_SYSTEM, user, model=MODEL_GENERATE)
    return {
        "main_text": result.get("main_text", ""),
        "sub_text": result.get("sub_text", ""),
    }
