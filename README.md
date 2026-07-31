# video-pipeline

Markdown記事（Zenn記事など）から、VOICEVOX実況動画に必要な素材を半自動生成するパイプライン。

```
Markdown記事
    │
    ├─▶ 台本エージェント   (生成→評価→修正、最大3回、90点閾値)
    │     └ 冒頭0:00〜1:00は概要・メリット訴求パート(単体でショート動画としても
    │       成立する内容)、残り約9分が詳細解説パート、という構成で生成する
    ├─▶ スライドエージェント (生成→評価→修正)   ※台本を参照
    └─▶ VOICEVOXテキストエージェント (生成→評価→修正) ※台本を参照
                    │
                    ▼
            総合エージェント（3つの整合性を採点。90点未満なら該当箇所を修正して再採点、最大3回）
                    │
                    ▼
            概要欄エージェント（確定した台本から概要文・目次・元記事リンク・ハッシュタグを生成）
                    │
                    ▼
     (任意) Geminiでスライド挿絵を生成 ※GENERATE_SLIDE_IMAGES有効時のみ
                    │
                    ▼
     output/20260731_153000/script.md
     output/20260731_153000/voicevox_script.txt
     output/20260731_153000/slides/slide_00_title.png, slide_01.png, ...
     output/20260731_153000/description.txt
     (実行するたびに output/<yyyymmdd_hhmmss>/ という新しいディレクトリに保存される)
```

このあと先は人間の作業:
1. `output/<実行時刻>/voicevox_script.txt` を見ながらVOICEVOXで音声を生成
2. `output/<実行時刻>/slides/` のPNG画像を画面素材として、DaVinci Resolveのタイムラインに並べる
3. 完成した動画をYouTubeにアップロードし、`output/<実行時刻>/description.txt` の内容を概要欄に貼る
   （元記事URLをCLIで渡していない場合はプレースホルダーになっているので、実URLに差し替える）

## セットアップ

```bash
uv sync
cp .env.example .env
# .env に ANTHROPIC_API_KEY を設定
```

## 実行方法

```bash
uv run video-pipeline --input articles/sample.md --title "機械学習ってなに？" \
  --article-url "https://zenn.dev/xxxxx/articles/xxxxx" \
  --generate-images
# または
uv run python -m video_pipeline.main --input articles/sample.md
```

`--article-url` を省略した場合、概要欄の元記事リンク部分にはプレースホルダーが入るので、
投稿時に手動で差し替える。

`--generate-images` を付けるとGemini(Nano Banana系)でスライドの挿絵を生成する。
`.env`に`GEMINI_API_KEY`の設定が必要（Claude用の`ANTHROPIC_API_KEY`とは別のキー）。
画像生成モデルは文字・数値の描画が不得意なため、数値や表を含むスライドには
挿絵を生成しない設計にしている（詳細は下記「スライド挿絵生成について」）。

実行するたびに `output/<yyyymmdd_hhmmss>/`（実行日時のディレクトリ）が新しく作られ、そこに成果物一式が保存される（ベースの `output` 部分は `--output-dir` で変更可）。

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `output/<実行時刻>/script.md` | ずんだもん×つむぎ形式の動画台本（シーン区切り・画面指示つき、冒頭1分は概要パート） |
| `output/<実行時刻>/voicevox_script.txt` | `[話者名] セリフ` 形式の読み上げ用テキスト |
| `output/<実行時刻>/slides/` | スライド画像一式(PNG、1枚=1スライド。表紙は`slide_00_title.png`) |
| `output/<実行時刻>/description.txt` | YouTube概要欄用テキスト（概要文・目次・元記事リンク・ハッシュタグ） |

## 構成

```
video_pipeline/
├── config.py            # モデル名・ループ回数・スコア閾値
├── claude_client.py      # Claude API呼び出しの共通処理（テキスト/JSON）
├── image_generator.py    # Gemini(Nano Banana系)によるスライド挿絵生成
├── io_utils.py            # ファイル入出力
├── loop.py                # 生成→評価→修正ループの共通ロジック
├── slide_image_builder.py # スライド内容(JSON、4レイアウト対応) -> PNG画像(Pillowで直接描画)
├── assets/fonts/          # Noto Sans JP(同梱フォント。OS依存を避けるため)
├── pipeline.py            # 全体のオーケストレーション
├── main.py                # CLIエントリーポイント
└── agents/
    ├── script_agent.py        # 台本の生成・評価・修正
    ├── slides_agent.py        # スライド内容(image_prompt含む)の生成・評価・修正
    ├── voicevox_agent.py      # VOICEVOX用テキストの生成・評価・修正
    ├── integration_agent.py   # 3つの横断的な整合性チェック
    └── description_agent.py   # YouTube概要欄（概要文・目次・リンク・タグ）の生成・評価・修正
```

## 調整できるパラメータ（`config.py`）

役割ごとにモデルを分けている（評価・整合性チェックは品質のゲート役なので最も強いモデルを、
VOICEVOXテキスト抽出のような機械的な作業は軽量モデルを割り当てる方針）。

- `MODEL_GENERATE`（環境変数 `CLAUDE_MODEL_GENERATE`）: 台本・スライドの生成/修正。デフォルト `claude-sonnet-5`
- `MODEL_EVALUATE`（環境変数 `CLAUDE_MODEL_EVALUATE`）: 評価・整合性チェック。デフォルト `claude-opus-4-8`
- `MODEL_EXTRACT`（環境変数 `CLAUDE_MODEL_EXTRACT`）: VOICEVOXテキストの機械的な抽出。デフォルト `claude-haiku-4-5-20251001`
- `MAX_REVISION_LOOPS`: 各エージェント（総合エージェントを含む）の生成→評価→修正ループの最大回数（デフォルト3）
- `SCORE_THRESHOLD`: この点数(0-100)以上で合格とみなす。総合エージェントの整合性スコアにも適用される（デフォルト90）
- `GENERATE_SLIDE_IMAGES`（環境変数）: スライド挿絵生成を有効にするか（デフォルトはオフ）
- `GEMINI_IMAGE_MODEL`（環境変数）: 挿絵生成に使うGeminiモデル。デフォルト `gemini-3.1-flash-image-preview`
  （Nano Banana 2。より高品質・高価な `gemini-3-pro-image-preview` に変更も可能）

## スライド挿絵生成について

`--generate-images` を有効にすると、`slides_agent`が各スライドの内容に応じて
`image_prompt`（英語、概念的な挿絵の指示）を付け、確定後に`image_generator.py`が
Gemini APIで実際の画像を生成して`output/<実行時刻>/images/`に保存、
`slide_image_builder.py`が該当スライド画像(`output/<実行時刻>/slides/`)の
右側に合成する。

方針として、**数値・表・コードなど正確性が必要なスライドにはimage_promptを
付けない**よう`slides_agent`に指示している。画像生成モデルは文字や数値を
正確に描くのが苦手なため、そうしたスライドは挿絵ではなく箇条書きのテキストで
正確に伝える設計にしている。挿絵が付くのは「汎用AIは借り物」のような
概念的な内容のスライドに限られる想定。

`GEMINI_API_KEY`が未設定、またはAPI呼び出しが失敗した場合は警告を出して
そのスライドの挿絵をスキップするだけで、パイプライン全体は止まらない。

## 既知の制約・今後の拡張候補

- 以前は`python-pptx`で`.pptx`を組み立てていたが、Mac上のPowerPointで開けない
  事例があったため、直接PNG画像を書き出す方式(`slide_image_builder.py`)に
  変更した。日本語フォントはOS依存を避けるためNoto Sans JPを同梱している
- スライドはNotebookLMのVideo Overviewを参考に、内容に応じて4種類のレイアウト
  （箇条書き/数値強調/引用/比較）を`slides_agent`が選んで生成する。
  配色やフォントサイズを変えたい場合は`slide_image_builder.py`の定数を編集する
- 概要欄エージェントは総合エージェントの整合性チェック対象には含めていない
  （確定した台本だけを見て作るため、台本と概要欄の食い違いはチェックされるが、
  スライド・VOICEVOXテキストとの整合性チェックは対象外）
- スライド挿絵(image_prompt)の妥当性は`slides_agent`の評価観点に軽く含めている
  程度で、総合エージェントの整合性チェック対象には含めていない
- VOICEVOXへの音声生成そのものは自動化していない
  （VOICEVOX ENGINEのHTTP APIを叩けば、ここも自動化できる余地がある）
- DaVinci Resolveでの編集・書き出しも対象外
  （ResolveのPython/Luaスクリプティングでタイムライン組み立てまで拡張する余地がある）
