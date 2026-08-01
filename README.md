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
     (任意) Geminiでスライド背景を生成し、その上に文字を重ねる ※GENERATE_SLIDE_IMAGES有効時のみ
                    │
                    ▼
     output/20260731_153000/script.md
     output/20260731_153000/voicevox_script.txt
     output/20260731_153000/slides/slide_00_title.png, slide_01.png, ...
     output/20260731_153000/description.txt
     (実行するたびに output/<yyyymmdd_hhmmss>/ という新しいディレクトリに保存される)
```

このあと先の作業:
- **選択肢A: 完全自動で動画まで作る**
  VOICEVOXアプリとffmpegを起動しておき、
  `uv run render-video --script output/<実行時刻>/script.md --slides-dir output/<実行時刻>/slides --output output/<実行時刻>/final_video.mp4`
  を実行すると、音声合成・スライド表示・話者ごとに色分けした字幕（つむぎ=黄色系、
  ずんだもん=緑系）の焼き込みまで自動で行い、1本のmp4が完成する
- **選択肢B: DaVinci Resolveで手動編集したい場合**
  1. `uv run voicevox-synthesize --input output/<実行時刻>/voicevox_script.txt` でセリフごとのWAV音声を一括生成
  2. `output/<実行時刻>/slides/` のPNG画像と生成した音声(WAV)を素材として、DaVinci Resolveのタイムラインに並べる

いずれの場合も、完成した動画をYouTubeにアップロードし、`output/<実行時刻>/description.txt` の内容を概要欄に貼る
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

`--title` を省略した場合、記事の見出し1（`# 〜`で始まる行）を自動でタイトルに使う。
見出し1が記事に無ければ「解説動画」になる。

`--article-url` を省略した場合、概要欄の元記事リンク部分にはプレースホルダーが入るので、
投稿時に手動で差し替える。

`--generate-images` を付けるとGemini(Nano Banana系)で各スライドの背景イラストを
生成し、その上に文字を重ねて描画する。`.env`に`GEMINI_API_KEY`の設定が必要
（Claude用の`ANTHROPIC_API_KEY`とは別のキー）。文字は常にPillowで正確に描画する
ため、背景画像に数値・専門用語を描かせる必要はない（詳細は下記「スライド背景生成について」）。

実行するたびに `output/<yyyymmdd_hhmmss>/`（実行日時のディレクトリ）が新しく作られ、そこに成果物一式が保存される（ベースの `output` 部分は `--output-dir` で変更可）。

## 音声合成（VOICEVOX ENGINE連携）

VOICEVOXアプリを起動しておくと（実体はHTTPサーバとして動作し、デフォルトで
`http://127.0.0.1:50021` で待ち受ける）、`voicevox_script.txt`から音声を
一括生成できる。

```bash
uv run voicevox-synthesize --input output/20260731_153000/voicevox_script.txt
```

出力先を省略すると、入力ファイルと同じ場所の`audio/`に保存される
（`--output-dir`で変更可、`--base-url`でVOICEVOXのURLも変更可）。

セリフ1行につき1つのWAVファイル（`001_つむぎ.wav`のように連番+話者名）を
生成し、DaVinci Resolveのタイムラインに上から順に並べやすくしている。
あわせて`audio/manifest.json`に、各ファイルと対応するテキストの一覧を書き出す。

話者ID（speaker_id）はVOICEVOXの`/speakers`エンドポイントから「つむぎ」
「ずんだもん」の名前で自動的に引き当てるため、ハードコードしていない
（VOICEVOXのバージョンや導入ライブラリによってIDが変わっても動作する）。
VOICEVOX上の登録名が「春日部つむぎ」のようにフルネームの場合も、
部分一致で解決するので台本上の短縮名（「つむぎ」）のままで問題ない。

## 動画の自動組み立て（字幕焼き込み込み）

VOICEVOXアプリと**ffmpeg**（要インストール。Macなら`brew install ffmpeg-full`。
`libass`(字幕焼き込み用)を含む必要があるため、軽量版の`brew install ffmpeg`
だと`ass`フィルタが無くエラーになる。詳細は下記トラブルシューティング参照）を
起動しておくと、台本・スライド・音声から色分け字幕つきの完成動画を1本のmp4に
組み立てられる。

```bash
uv run render-video \
  --script output/20260731_153000/script.md \
  --slides-dir output/20260731_153000/slides \
  --output output/20260731_153000/final_video.mp4
```

流れ:
1. `script_parser.py`が`script.md`を正規表現で決定的にパースし、
   「どのセリフがどのシーンに属するか」を確定させる
   （`voicevox_agent`のLLM抽出は経由しない。台本と音声・字幕を確実に一致させるため）
2. 各セリフをVOICEVOX ENGINEで直接音声合成し、長さを計測
3. `slides/manifest.json`の`scene_number`をもとに、各スライドの表示時間
   （＝対応するシーンの音声の合計時間）を計算。1つのシーンに複数のスライドが
   割り当てられていれば均等割り、対応するスライドが無いシーンは直前の
   スライドの表示を延長する
4. セリフごとのタイミングで**ASS形式**の字幕を生成する。話者ごとに
   スタイルを分け、つむぎ＝黄色系(`&H0000E5FF`)、ずんだもん＝緑系(`&H0055AA55`)
   で色分けする（`video_assembler.SUBTITLE_STYLE_COLORS`で変更可）
5. ffmpegで (a)音声を結合 (b)スライド画像を表示時間通りに並べた無音動画を作成
   (c) 動画+音声+字幕を1本のmp4に合成する

日本語フォントはfontconfig経由の解決に頼らず、`assets/fonts/`に同梱した
静的なNoto Sans JP(Bold/Regular)を`fontsdir`オプションで直接指定している
（可変フォントだとfontconfigがウェイトを正しく解決できず文字化けする
事例があったため、字幕用には別途静的フォントを追加で同梱している）。

`--script`と`--slides-dir`はvideo-pipelineの出力からそのまま使うが、
音声合成自体は`render-video`が内部で直接行う（`voicevox-synthesize`や
`voicevox_script.txt`は経由しない）。これは字幕・シーン番号と音声を
確実に一致させるための設計で、`voicevox-synthesize`（DaVinci Resolveで
手動編集したい場合向け）とは独立した経路になっている。

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `output/<実行時刻>/script.md` | ずんだもん×つむぎ形式の動画台本（シーン区切り・画面指示つき、冒頭1分は概要パート） |
| `output/<実行時刻>/voicevox_script.txt` | `[話者名] セリフ` 形式の読み上げ用テキスト |
| `output/<実行時刻>/slides/` | スライド画像一式(PNG、1枚=1スライド。表紙は`slide_00_title.png`) |
| `output/<実行時刻>/backgrounds/` | （背景生成有効時）Geminiで生成した背景イラストの元画像 |
| `output/<実行時刻>/description.txt` | YouTube概要欄用テキスト（概要文・目次・元記事リンク・ハッシュタグ） |
| `output/<実行時刻>/audio/` | （`voicevox-synthesize`実行後）セリフごとのWAV音声+manifest.json |
| `output/<実行時刻>/final_video.mp4` | （`render-video`実行後）色分け字幕つきの完成動画 |

## 構成

```
video_pipeline/
├── config.py            # モデル名・ループ回数・スコア閾値
├── claude_client.py      # Claude API呼び出しの共通処理（テキスト/JSON）
├── image_generator.py    # Gemini(Nano Banana系)によるスライド背景生成
├── voicevox_client.py     # ローカルVOICEVOX ENGINEのHTTP APIクライアント
├── script_parser.py       # script.mdをシーン・セリフに決定的にパース(正規表現)
├── video_assembler.py     # 台本+スライド+音声から字幕付き動画をffmpegで組み立て
├── io_utils.py            # ファイル入出力
├── loop.py                # 生成→評価→修正ループの共通ロジック
├── slide_image_builder.py # スライド内容(JSON、4レイアウト対応) -> PNG画像(Pillowで直接描画)
├── assets/fonts/          # Noto Sans JP(スライド用:可変フォント、字幕用:静的Bold/Regular)
├── pipeline.py            # 全体のオーケストレーション
├── main.py                # CLIエントリーポイント(video-pipeline)
├── synthesize_audio.py    # CLIエントリーポイント(voicevox-synthesize)
├── render_video.py        # CLIエントリーポイント(render-video)
└── agents/
    ├── script_agent.py        # 台本の生成・評価・修正
    ├── slides_agent.py        # スライド内容(background_prompt, scene_number含む)の生成・評価・修正
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
- `GENERATE_SLIDE_IMAGES`（環境変数）: スライド背景生成を有効にするか（デフォルトはオフ）
- `GEMINI_IMAGE_MODEL`（環境変数）: 背景生成に使うGeminiモデル。デフォルト `gemini-3.1-flash-image-preview`
  （Nano Banana 2。より高品質・高価な `gemini-3-pro-image-preview` に変更も可能）

## スライド背景生成について

`--generate-images` を有効にすると、`slides_agent`がほぼ全スライドに
`background_prompt`（英語、抽象的な情景の指示。文字・数字は含めない）を付け、
確定後に`image_generator.generate_slide_background()`がGemini APIで実際の
画像を生成して`output/<実行時刻>/backgrounds/`に保存する。

生成した背景は`slide_image_builder.py`がスライド全面に敷いた上で、可読性の
ために不透明度80%の白いスクリム(半透明パネル)を重ね、その上からタイトル・
箇条書き・数値などのテキストをPillowで直接描画する。つまり**背景の絵柄が
どうであれ、実際に表示される数値や専門用語は常に正確**という構造になっている
（NotebookLMのように画像自体に文字を焼き込む方式とは違い、文字とビジュアルの
生成経路を完全に分離している）。

複数スライドの絵柄がバラバラにならないよう、`slides_agent`が書いた
内容依存のプロンプトに対して、`image_generator.BACKGROUND_STYLE_SUFFIX`
（配色・タッチを固定した文言）をコード側で必ず付け足している。

`GEMINI_API_KEY`が未設定、またはAPI呼び出しが失敗した場合は警告を出して
その1枚の背景生成だけをスキップし、単色背景にフォールバックする
（パイプライン全体は止まらない）。

背景生成はほぼ全スライドに対して行われるため、`--generate-images`を使うと
Gemini APIの呼び出し回数・コストが動画1本あたりスライド枚数分（10〜16回程度）
発生する点は把握しておく。

## トラブルシューティング

実際に開発中に遭遇したトラブルと対処法をまとめている。

### `ModuleNotFoundError: No module named 'video_pipeline'`

`video-pipeline`・`voicevox-synthesize`・`render-video`のいずれかを実行したときに
このエラーが出て、`uv run python -c "import video_pipeline"`は成功する場合、
インストール済みのコンソールスクリプト（`.venv/bin/`以下）だけが壊れている状態。

**すぐ動かす回避策**: コンソールスクリプトを経由せず、モジュールとして直接実行する。

```bash
uv run python -m video_pipeline.main --input articles/sample.md
uv run python -m video_pipeline.synthesize_audio --input output/<実行時刻>/voicevox_script.txt
uv run python -m video_pipeline.render_video --script output/<実行時刻>/script.md --slides-dir output/<実行時刻>/slides --output output/<実行時刻>/final_video.mp4
```

**根本的な直し方**: venvを作り直す。

```bash
rm -rf .venv uv.lock
uv cache clean
uv sync
uv run video-pipeline --help
```

**再発する・作り直してもすぐ壊れる場合**: プロジェクトの置き場所を疑う。
`~/Desktop`や`~/Documents`配下で作業していると、Macの「iCloud Drive: デスクトップと
書類のフォルダ」同期が原因で`.venv`内の大量の小さいファイルが同期対象になり、
不定期に退避（evict）されて壊れることがある（動いたり動かなかったりする、
コマンドによって成功・失敗がバラつく、という症状が特徴）。

```
システム設定 → Apple ID名 → iCloud → iCloud Drive → オプション
→「デスクトップと書類のフォルダ」がオンになっていないか確認
```

オンになっていた場合は、プロジェクトをiCloud同期の対象外の場所に移動する。

```bash
mkdir -p ~/dev
mv ~/Desktop/video-pipeline ~/dev/video-pipeline
cd ~/dev/video-pipeline
rm -rf .venv uv.lock
uv sync
```

### `warning: VIRTUAL_ENV=... does not match the project environment path`

以前に別の場所（例: 移動前の`~/Desktop/video-pipeline/.venv`）を`source .venv/bin/activate`
などで手動アクティベートしたままになっていると出る警告。`uv run`は正しく無視して
現在のプロジェクトの`.venv`を使うので実害はない。気になる場合はターミナルを
開き直すか、`deactivate`してから`uv run`を実行すると警告が消える。

### `話者「つむぎ」が見つかりません`

VOICEVOXアプリでの登録名が「春日部つむぎ」のようにフルネームだと、台本上の
短縮名「つむぎ」と完全一致しないために起きていた。`voicevox_client.resolve_speaker_id`
は完全一致→部分一致の順で探すよう修正済みなので、最新版なら発生しないはず。
それでも起きる場合はエラーメッセージに表示される「VOICEVOXに登録されている話者」
一覧を確認する。

### `[Errno 2] No such file or directory: 'ffmpeg'`

`render-video`はffmpegを外部コマンドとして呼び出すため、別途インストールが必要
（Pythonの依存関係(`uv sync`)には含まれない）。字幕焼き込みに`libass`が必要なので、
軽量版の`ffmpeg`ではなく`ffmpeg-full`を入れる（理由は次項参照）。

```bash
brew install ffmpeg-full
which ffmpeg   # パスが表示されればOK
ffmpeg -filters | grep ass   # ass / subtitles が表示されればOK
```

Homebrew自体が無い場合は先に https://brew.sh の案内に従ってインストールする。

### `[AVFilterGraph] No such filter: 'ass'` / `No option name near '...captions.ass:fontsdir=...'`

**根本原因はHomebrewのffmpegが軽量版になっていること。** 2026年以降、Homebrewの
`ffmpeg`フォーミュラ（`ffmpeg@8`）は主要コーデックのみの軽量版がデフォルトになり、
`libass`（字幕焼き込みライブラリ）が含まれていない。この場合`ass`/`subtitles`
フィルタ自体が存在せず、`No such filter: 'ass'`というエラーになる
（バージョンや状況によっては、クォートの書き方の問題に見える
`No option name near ...`という紛らわしいエラーが先に出ることもある）。

`ffmpeg -filters | grep ass` を実行して何も表示されなければ`libass`無しの
軽量版が入っている。**機能フル版に入れ替える**。

```bash
brew uninstall ffmpeg
brew install ffmpeg-full
ffmpeg -filters | grep ass   # ass / subtitles が表示されればOK
```

`ffmpeg-full`が正しくリンクされない場合は`brew link --overwrite ffmpeg-full`を試す。

### `output/<実行時刻>/slides/manifest.json が見つかりません`

`render-video`が要求する`slides/manifest.json`（各スライドの`scene_number`を
記録したファイル）は、それが導入された時点より前の`video-pipeline`実行結果には
存在しない。古い`output/`ディレクトリに対して`render-video`を実行しようとすると
このエラーになる。`video-pipeline`を再実行して新しい`output/<実行時刻>/`を
作り直してから`render-video`を実行する。

## 既知の制約・今後の拡張候補

- スライド数が多い記事ではJSON応答が長くなり、`max_tokens`に到達して出力が
  途中で切れることがあった。`claude_client.py`がレスポンスの`stop_reason`を
  見て自動的にトークン上限を倍増しながら再生成するようにして対処している
  （`MAX_TOKENS_CEILING`まで。それでも切れる場合は警告を出しつつ不完全な
  内容のまま処理を続ける）

- 以前は`python-pptx`で`.pptx`を組み立てていたが、Mac上のPowerPointで開けない
  事例があったため、直接PNG画像を書き出す方式(`slide_image_builder.py`)に
  変更した。日本語フォントはOS依存を避けるためNoto Sans JPを同梱している
- スライドはNotebookLMのVideo Overviewを参考に、内容に応じて4種類のレイアウト
  （箇条書き/数値強調/引用/比較）を`slides_agent`が選んで生成する。
  配色やフォントサイズを変えたい場合は`slide_image_builder.py`の定数を編集する
- 概要欄エージェントは総合エージェントの整合性チェック対象には含めていない
  （確定した台本だけを見て作るため、台本と概要欄の食い違いはチェックされるが、
  スライド・VOICEVOXテキストとの整合性チェックは対象外）
- スライド背景(background_prompt)の妥当性は`slides_agent`の評価観点に軽く含めている
  程度で、総合エージェントの整合性チェック対象には含めていない
- `voicevox_agent`のプロンプトでは`"[話者名] セリフ"`(角括弧あり)の形式を
  指示しているが、実際には軽量モデル(MODEL_EXTRACT)が角括弧なしの
  `"話者名 セリフ"`形式で出力することがあった。`voicevox_client.py`の
  パーサーは既知の話者名（つむぎ/ずんだもん）をもとに両方の形式に対応している
- DaVinci Resolveでの編集・書き出しは対象外
  （ResolveのPython/Luaスクリプティングでタイムライン組み立てまで拡張する余地がある）
- `render-video`はscript.mdの見出し形式「### シーン<N>：〜」とセリフ形式
  「つむぎ「〜」」「ずんだもん「〜」」に強く依存する。script_agentのプロンプトが
  変わってこの形式が崩れると、`script_parser.py`がセリフを抽出できなくなる
- `render-video`実行時に生成される音声は`voicevox-synthesize`とは別経路
  （scene_numberとの整合性を優先したため）。同じ台本に対して両方を実行すると
  VOICEVOX ENGINEへの音声合成が二重に走る点は把握しておく
- 1つのシーンに複数スライドが割り当てられている場合、表示時間は均等割りに
  している（セリフの長さに応じた比例配分などはしていない）
