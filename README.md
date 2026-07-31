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
     output/script.md / output/voicevox_script.txt
     output/slides.pptx / output/description.txt
```

このあと先は人間の作業:
1. `output/voicevox_script.txt` を見ながらVOICEVOXで音声を生成
2. `output/slides.pptx` を画面素材として、DaVinci Resolveで音声と合わせて編集
3. 完成した動画をYouTubeにアップロードし、`output/description.txt` の内容を概要欄に貼る
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
  --article-url "https://zenn.dev/xxxxx/articles/xxxxx"
# または
uv run python -m video_pipeline.main --input articles/sample.md
```

`--article-url` を省略した場合、概要欄の元記事リンク部分にはプレースホルダーが入るので、
投稿時に手動で差し替える。

出力は `output/` 以下に生成される（`--output-dir` で変更可）。

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `output/script.md` | ずんだもん×つむぎ形式の動画台本（シーン区切り・画面指示つき、冒頭1分は概要パート） |
| `output/voicevox_script.txt` | `[話者名] セリフ` 形式の読み上げ用テキスト |
| `output/slides.pptx` | 台本のシーンに対応したスライド（タイトル・箇条書き・ノート） |
| `output/description.txt` | YouTube概要欄用テキスト（概要文・目次・元記事リンク・ハッシュタグ） |

## 構成

```
video_pipeline/
├── config.py            # モデル名・ループ回数・スコア閾値
├── claude_client.py      # Claude API呼び出しの共通処理（テキスト/JSON）
├── io_utils.py            # ファイル入出力
├── loop.py                # 生成→評価→修正ループの共通ロジック
├── pptx_builder.py        # スライド内容(JSON) -> .pptx
├── pipeline.py            # 全体のオーケストレーション
├── main.py                # CLIエントリーポイント
└── agents/
    ├── script_agent.py        # 台本の生成・評価・修正
    ├── slides_agent.py        # スライド内容の生成・評価・修正
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

## 既知の制約・今後の拡張候補

- スライドはシンプルな「タイトル+箇条書き」構成。図解画像の自動挿入はしていない
  （元記事のmermaid図はスクリーンショット等で人間が追加する想定）
- 概要欄エージェントは総合エージェントの整合性チェック対象には含めていない
  （確定した台本だけを見て作るため、台本と概要欄の食い違いはチェックされるが、
  スライド・VOICEVOXテキストとの整合性チェックは対象外）
- VOICEVOXへの音声生成そのものは自動化していない
  （VOICEVOX ENGINEのHTTP APIを叩けば、ここも自動化できる余地がある）
- DaVinci Resolveでの編集・書き出しも対象外
  （ResolveのPython/Luaスクリプティングでタイムライン組み立てまで拡張する余地がある）
# video-pipeline
