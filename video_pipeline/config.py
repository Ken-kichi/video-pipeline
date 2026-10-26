"""パイプライン全体の設定値。"""

import os

from dotenv import load_dotenv

# 以下の設定値はimport時にos.environから読み込まれる。各CLIのmain()でload_dotenv()
# を呼んでも、それより前にconfigがimportされているため.envの値が反映されなかった
# (CLAUDE_MODEL_*等を.envに書いても無視されていた)。そのためここで先に読み込む。
# 既にシェルで設定されている環境変数は上書きしない(load_dotenvの標準動作)。
load_dotenv()

# 役割ごとにモデルを分ける。全て環境変数で上書き可能。
#
# - GENERATE: 台本・スライドなど、創造性と一貫性が必要な生成/修正作業
# - EVALUATE: スコアリング・整合性チェックなど、ループの品質保証を担う"審査員"役
#             Haiku 5.5でも欠陥の検出と指摘の質は同等だったが、点数がSonnetより
#             約20点厳しく合否の分離も不安定で(実験: 欠陥を注入した台本の評価)、
#             通過点80のままだと修正ループが毎回上限まで回りコストが逆に増えるため、
#             台本などの評価はSonnetにしている。コストは次のEFFORTで抑える
# - EXTRACT:  VOICEVOX用テキスト抽出など、フォーマットに沿って機械的に整形するだけの作業
MODEL_GENERATE = os.environ.get("CLAUDE_MODEL_GENERATE", "claude-sonnet-5")
MODEL_EVALUATE = os.environ.get("CLAUDE_MODEL_EVALUATE", "claude-sonnet-5")
MODEL_EXTRACT = os.environ.get("CLAUDE_MODEL_EXTRACT", "claude-haiku-5-5")

# 役割ごとの思考量(effort: low/medium/high/xhigh/max)。思考は出力トークンとして
# 課金され、Sonnet 5の評価では1回あたり出力約8,000トークンが評価コストの約2/3を
# 占めていた(未指定時のデフォルトはhigh)。空・未設定ならAPIのデフォルトに任せる。
# 環境変数 CLAUDE_EFFORT_GENERATE/EVALUATE/EXTRACT で指定する(空文字を指定すると
# 未指定扱いになり、APIのデフォルトに戻る)。
#
# EVALUATEのデフォルトはmedium。台本評価の実験(実在台本2本×正常/欠陥3種、Sonnet 5)で、
#   - medium: highと同等(欠陥検出6/6、元台本の点が3点低い程度、合否の分離幅は
#             highの9点に対して10点)。出力トークンは大きく減り、所要時間は約半分
#   - low:    元台本まで48〜62点に落ち、欠陥入りとの分離幅が0点で評価として使えない
# だったため。スライド評価・総合チェックにも同じ値が適用される(こちらは未検証)。
# GENERATE/EXTRACTは品質への影響を確認していないため、デフォルトは未指定のまま
EFFORT_GENERATE = os.environ.get("CLAUDE_EFFORT_GENERATE") or None
EFFORT_EVALUATE = os.environ.get("CLAUDE_EFFORT_EVALUATE", "medium") or None
EFFORT_EXTRACT = os.environ.get("CLAUDE_EFFORT_EXTRACT") or None

# 総合エージェントのクロスチェックに使うGemini側の評価モデル。Claudeとは別会社の
# モデルに同じ観点でチェックさせ、単一モデルが見落としがちな不整合を拾う狙い。
# GEMINI_API_KEYが未設定の場合、総合エージェントはこのモデルを使わずClaude単独の
# チェックにフォールバックする
MODEL_EVALUATE_GEMINI = os.environ.get(
    "GEMINI_MODEL_EVALUATE", "gemini-3.1-pro-preview"
)

# 生成→評価→修正ループの最大試行回数（ai-dev-agent構想と同じく上限3回）
MAX_REVISION_LOOPS = 3

# 総合エージェント(台本とスライドの横断チェック)の最大ラウンド数。
# 1ラウンドで「Claudeのチェック+Geminiのチェック+結果の統合」と複数回の呼び出しが
# 発生し、指摘があれば台本・スライドの修正までする。台本・スライドは既に個別の
# 評価ループを通っているため、MAX_REVISION_LOOPS(3)よりも少なくしてコストを抑える。
# 1にすると「チェックして報告するだけ」で修正は行われないため、最小値は2
MAX_INTEGRATION_LOOPS = int(os.environ.get("MAX_INTEGRATION_LOOPS", "2"))

# このスコア(0-100)以上になったらループを打ち切って採用する
SCORE_THRESHOLD = 80

# 応答の最大トークン数（台本・スライド内容など長文生成用）。
# claude_client.pyはストリーミングAPIで呼び出しており、非ストリーミングAPIに
# あったmax_tokens=21333の上限は適用されない。これでも打ち切られた場合は
# claude_client.pyが自動でトークン上限をMAX_TOKENS_CEILING(64000)まで
# 倍増しながら再生成する
MAX_TOKENS = 20000

# スライドの挿絵をGemini(Nano Banana系)で生成するかどうか。
# 別APIの課金が発生するため、デフォルトはオフ。CLIの --generate-images か
# 環境変数 GENERATE_SLIDE_IMAGES=1 で有効化する。
GENERATE_SLIDE_IMAGES = os.environ.get("GENERATE_SLIDE_IMAGES", "0") == "1"

# 使用するGemini画像生成モデル。
# gemini-2.5-flash-image / gemini-3.1-flash-image-preview(Nano Banana 2, 安価) /
# gemini-3-pro-image-preview(Nano Banana Pro, 高品質・文字精度が高いが高価)
GEMINI_IMAGE_MODEL = os.environ.get(
    "GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview"
)

# サムネイルは背景に文字を直接焼き込む(通常のスライド背景と違い文字精度が
# 重要)ため、1本の動画につき1回だけの生成であることも踏まえ、デフォルトで
# より文字精度の高い(高価な)モデルを使う
GEMINI_THUMBNAIL_MODEL = os.environ.get(
    "GEMINI_THUMBNAIL_MODEL", "gemini-3-pro-image-preview"
)

# 立ち絵イラストレーターのクレジット表記。PSDファイル自体からは著作者情報を
# 読み取れないため、.envや環境変数で明示的に設定する必要がある
# (配布元のページ・README等に記載されている表記をそのまま使う想定)。
# 未設定の場合は概要欄にプレースホルダーが入る。
TSUMUGI_ILLUSTRATOR_CREDIT = os.environ.get(
    "TSUMUGI_ILLUSTRATOR_CREDIT", "坂本アヒル 様"
)
ZUNDAMON_ILLUSTRATOR_CREDIT = os.environ.get(
    "ZUNDAMON_ILLUSTRATOR_CREDIT", "坂本アヒル 様"
)

# チャンネル登録導線用のチャンネルURL。動画ごとに変わらない固定値なので、
# LLMに書かせず概要欄に決定的に付け足す(実在しないURLの創作を避けるため)。
CHANNEL_URL = os.environ.get(
    "CHANNEL_URL", "https://www.youtube.com/channel/UCc9QZS9y-NknFMxPQp5egtg"
)

# 概要欄に元記事を紹介し、動画の最後でも「概要欄の元記事を見てほしい」と
# 案内するかどうか。元記事を用意していない回はCLIの --no-mention-article か
# 環境変数 MENTION_ARTICLE=0 で無効化できる。
MENTION_ARTICLE = os.environ.get("MENTION_ARTICLE", "1") == "1"
