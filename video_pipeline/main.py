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
    parser.add_argument("--title", default="解説動画", help="スライドの表紙タイトル")
    parser.add_argument(
        "--article-url",
        default=None,
        help="概要欄に貼る元記事(Zenn/note等)のURL。未指定ならプレースホルダーを挿入",
    )
    args = parser.parse_args()

    run_pipeline(args.input, args.output_dir, args.title, args.article_url)


if __name__ == "__main__":
    main()
