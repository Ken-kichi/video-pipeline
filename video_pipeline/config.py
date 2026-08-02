"""パイプライン全体の設定値。"""

import os

# 役割ごとにモデルを分ける。全て環境変数で上書き可能。
#
# - GENERATE: 台本・スライドなど、創造性と一貫性が必要な生成/修正作業
# - EVALUATE: スコアリング・整合性チェックなど、ループの品質保証を担う"審査員"役
#             ここが弱いと生成側の品質が良くてもループが機能しなくなるため、
#             最も強いモデルを割り当てる
# - EXTRACT:  VOICEVOX用テキスト抽出など、フォーマットに沿って機械的に整形するだけの作業
MODEL_GENERATE = os.environ.get("CLAUDE_MODEL_GENERATE", "claude-sonnet-5")
MODEL_EVALUATE = os.environ.get("CLAUDE_MODEL_EVALUATE", "claude-opus-4-8")
MODEL_EXTRACT = os.environ.get(
    "CLAUDE_MODEL_EXTRACT", "claude-haiku-4-5-20251001")

# 生成→評価→修正ループの最大試行回数（ai-dev-agent構想と同じく上限3回）
MAX_REVISION_LOOPS = 3

# このスコア(0-100)以上になったらループを打ち切って採用する
SCORE_THRESHOLD = 90

# 応答の最大トークン数（台本・スライド内容など長文生成用）。
# スライドが多い記事ではJSON全体が大きくなるため、余裕を持たせている。
# これでも打ち切られた場合はclaude_client.pyが自動でトークン上限を増やして再生成する
MAX_TOKENS = 8192

# スライドの挿絵をGemini(Nano Banana系)で生成するかどうか。
# 別APIの課金が発生するため、デフォルトはオフ。CLIの --generate-images か
# 環境変数 GENERATE_SLIDE_IMAGES=1 で有効化する。
GENERATE_SLIDE_IMAGES = os.environ.get("GENERATE_SLIDE_IMAGES", "0") == "1"

# 使用するGemini画像生成モデル。
# gemini-2.5-flash-image / gemini-3.1-flash-image-preview(Nano Banana 2, 安価) /
# gemini-3-pro-image-preview(Nano Banana Pro, 高品質・文字精度が高いが高価)
GEMINI_IMAGE_MODEL = os.environ.get(
    "GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview")

# 立ち絵イラストレーターのクレジット表記。PSDファイル自体からは著作者情報を
# 読み取れないため、.envや環境変数で明示的に設定する必要がある
# (配布元のページ・README等に記載されている表記をそのまま使う想定)。
# 未設定の場合は概要欄にプレースホルダーが入る。
TSUMUGI_ILLUSTRATOR_CREDIT = os.environ.get(
    "TSUMUGI_ILLUSTRATOR_CREDIT", "坂本アヒル 様")
ZUNDAMON_ILLUSTRATOR_CREDIT = os.environ.get(
    "ZUNDAMON_ILLUSTRATOR_CREDIT", "坂本アヒル 様")
