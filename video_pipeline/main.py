"""CLIエントリーポイント。

使い方:
  uv run video-pipeline --input articles/sample.md
  uv run python -m video_pipeline.main --input articles/sample.md --title "機械学習ってなに？"
"""

import argparse

from dotenv import load_dotenv

from video_pipeline.pipeline import run_pipeline


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Markdown記事から動画台本・スライド・VOICEVOX用テキストを自動生成する"
    )
    parser.add_argument("--input", required=True, help="入力Markdown記事のパス")
    parser.add_argument("--output-dir", default="output", help="出力先ディレクトリ（デフォルト: output）")
    parser.add_argument(
        "--title",
        default=None,
        help="スライドの表紙タイトル。未指定なら記事の見出し1(# )を自動で使う",
    )
    parser.add_argument(
        "--article-url",
        default=None,
        help="概要欄に貼る元記事(Zenn/note等)のURL。未指定ならプレースホルダーを挿入",
    )
    parser.add_argument(
        "--generate-images",
        action="store_true",
        default=None,
        help="Gemini(GEMINI_API_KEY必須)でスライドの挿絵を生成する。省略時は環境変数"
        "GENERATE_SLIDE_IMAGESの設定に従う（デフォルトはオフ）",
    )
    args = parser.parse_args()

    run_pipeline(args.input, args.output_dir, args.title, args.article_url, args.generate_images)


if __name__ == "__main__":
    main()
