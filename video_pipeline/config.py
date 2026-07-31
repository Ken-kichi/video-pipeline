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
MODEL_EXTRACT = os.environ.get("CLAUDE_MODEL_EXTRACT", "claude-haiku-4-5-20251001")

# 生成→評価→修正ループの最大試行回数（ai-dev-agent構想と同じく上限3回）
MAX_REVISION_LOOPS = 3

# このスコア(0-100)以上になったらループを打ち切って採用する
SCORE_THRESHOLD = 90

# 応答の最大トークン数（台本・スライド内容など長文生成用）
MAX_TOKENS = 4096
