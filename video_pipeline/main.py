"""CLIエントリーポイント。

使い方:
  uv run video-pipeline --input articles/sample.md
  uv run video-pipeline    # 引数を省略すると全て対話的に選べる
  uv run python -m video_pipeline.main --input articles/sample.md --title "機械学習ってなに？"
"""

import argparse

from dotenv import load_dotenv

from video_pipeline.config import GENERATE_SLIDE_IMAGES as DEFAULT_GENERATE_IMAGES
from video_pipeline.config import MENTION_ARTICLE as DEFAULT_MENTION_ARTICLE
from video_pipeline.interactive import (
    confirm,
    pick_article,
    select_from_list,
    text_input,
)
from video_pipeline.pipeline import run_pipeline


def _resolve_title(cli_title: str | None) -> str | None:
    """--titleが未指定なら、AI生成か手入力かを対話的に選ばせる。"""
    if cli_title is not None:
        return cli_title
    choice = select_from_list(
        ["AIにキャッチーなタイトルを考えてもらう", "自分でタイトルを入力する"],
        "動画のYouTubeタイトル（スライド表紙にも使う）はどうしますか？",
    )
    if choice == "自分でタイトルを入力する":
        return text_input("タイトルを入力してください:")
    return (
        None  # AI生成(pipeline側の既定動作)。非対話環境でもこのフォールバックになる
    )


def _resolve_article_url(cli_url: str | None) -> str | None:
    """--article-urlが未指定なら、プレースホルダーのままか入力するかを対話的に選ばせる。"""
    if cli_url is not None:
        return cli_url
    choice = select_from_list(
        ["概要欄はプレースホルダーのままにする", "元記事のURLを入力する"],
        "概要欄に貼る元記事URLはどうしますか？",
    )
    if choice == "元記事のURLを入力する":
        return text_input("元記事(Zenn/note等)のURLを入力してください:")
    return None  # プレースホルダーのまま。非対話環境でもこのフォールバックになる


def _resolve_mention_article(cli_value: bool | None) -> bool:
    """--mention-article/--no-mention-articleが未指定なら対話的に選ばせる。"""
    if cli_value is not None:
        return cli_value
    return confirm(
        "概要欄に元記事を紹介し、動画内でも「概要欄を見てほしい」と案内しますか？",
        default=DEFAULT_MENTION_ARTICLE,
    )


def _resolve_generate_images(cli_value: bool | None) -> bool | None:
    """--generate-imagesが未指定なら、Yes/Noを対話的に選ばせる。"""
    if cli_value is not None:
        return cli_value
    return confirm(
        "Gemini(GEMINI_API_KEY必須)でスライドの背景画像を生成しますか？",
        default=DEFAULT_GENERATE_IMAGES,
    )


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Markdown記事から動画台本・スライド・VOICEVOX用テキストを自動生成する"
    )
    parser.add_argument(
        "--input",
        default=None,
        help="入力Markdown記事のパス。省略するとarticles/*.mdから対話的に選べる",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="出力先ディレクトリ（デフォルト: output）",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="スライドの表紙タイトル。未指定なら対話的に選べる（自動検出 or 手入力）",
    )
    parser.add_argument(
        "--article-url",
        default=None,
        help="概要欄に貼る元記事(Zenn/note等)のURL。未指定なら対話的に選べる",
    )
    parser.add_argument(
        "--mention-article",
        dest="mention_article",
        action="store_true",
        default=None,
        help="概要欄に元記事を紹介し、動画内でも案内する。未指定なら対話的に選べる",
    )
    parser.add_argument(
        "--no-mention-article",
        dest="mention_article",
        action="store_false",
        help="概要欄への元記事紹介と動画内での案内を行わない",
    )
    parser.add_argument(
        "--generate-images",
        action="store_true",
        default=None,
        help="Gemini(GEMINI_API_KEY必須)でスライドの挿絵を生成する。未指定なら対話的に選べる",
    )
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        picked = pick_article()
        if picked is None:
            raise SystemExit(
                "入力記事が指定されていません。--inputで指定するか、"
                "articles/ディレクトリに.mdファイルを置いた状態で対話端末から実行してください。"
            )
        input_path = str(picked)
        print(f"選択された記事: {input_path}")

    title = _resolve_title(args.title)
    mention_article = _resolve_mention_article(args.mention_article)
    article_url = _resolve_article_url(args.article_url) if mention_article else None
    generate_images = _resolve_generate_images(args.generate_images)

    run_pipeline(
        input_path,
        args.output_dir,
        title,
        article_url,
        generate_images,
        mention_article,
    )


if __name__ == "__main__":
    main()
