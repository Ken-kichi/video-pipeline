"""既存のscript.mdからサムネイル(16:9, 1280x720)だけを生成するCLI。

`video-pipeline`はサムネイル生成を含まない。サムネイルの文言や背景だけ
作り直したい場合に、台本・スライド・概要欄などの他のエージェントを
無駄に動かさずに済むよう、この専用コマンドに分離している
(呼び出すエージェントはthumbnail_agentのみ。単発のClaude API呼び出し1回)。

画像生成は2段階のフォールバック構成になっている:
  1. GEMINI_API_KEYが使えれば、Geminiに背景・文字を丸ごと生成させる
     (推奨。文字と背景が一体で描かれるため、Pillowで別々に重ねる方式より
     レイアウトが自然になる。実際に、キャラクター立ち絵とPillow描画の
     文字が重なってしまう不具合が起きたことがある)
  2. Geminiが使えない/失敗した場合は、Pillowでテキスト・キャラクター立ち絵
     (あれば)を個別に重ねて描く(build_thumbnail)

使い方:
  uv run generate-thumbnail --script output/20260731_153000/script.md
  uv run generate-thumbnail    # --scriptを省略するとoutput/*/から対話的に選べる
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

from video_pipeline.agents import thumbnail_agent
from video_pipeline.config import GENERATE_SLIDE_IMAGES
from video_pipeline.image_generator import generate_slide_background
from video_pipeline.interactive import confirm, pick_output_run
from video_pipeline.thumbnail_generator import build_thumbnail, generate_thumbnail_with_gemini


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="既存のscript.mdからサムネイル(16:9)だけを生成する"
    )
    parser.add_argument(
        "--script",
        default=None,
        help="script.mdのパス。省略するとoutput/*/から対話的に選べる",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="出力するサムネイル画像のパス(省略時はscript.mdと同じディレクトリのthumbnail.png)",
    )
    parser.add_argument(
        "--generate-images",
        action="store_true",
        default=None,
        help="Gemini(GEMINI_API_KEY必須)で背景・文字を丸ごと生成する。未指定なら対話的に選べる",
    )
    parser.add_argument(
        "--no-gemini-fulltext",
        action="store_true",
        help="Geminiを使う場合でも、文字はGeminiに焼き込ませずPillowで別途重ねる"
        "(従来方式。Geminiの文字精度に不安がある場合向け)",
    )
    args = parser.parse_args()

    script_path = Path(args.script) if args.script else None
    if script_path is None:
        run_dir = pick_output_run()
        if run_dir is None:
            raise SystemExit(
                "対象のscript.mdが指定されていません。--scriptで指定するか、"
                "output/ディレクトリに実行結果がある状態で対話端末から実行してください。"
            )
        print(f"選択された実行結果: {run_dir.name}")
        script_path = run_dir / "script.md"

    if not script_path.exists():
        raise FileNotFoundError(f"台本ファイルが見つかりません: {script_path}")

    output_path = Path(args.output) if args.output else script_path.parent / "thumbnail.png"

    generate_images = args.generate_images
    if generate_images is None:
        generate_images = confirm(
            "Gemini(GEMINI_API_KEY必須)で背景・文字を丸ごと生成しますか？"
            "（推奨。使わない場合はPillowで簡易的に組み立てる）",
            default=GENERATE_SLIDE_IMAGES,
        )

    script_text = script_path.read_text(encoding="utf-8")

    print("=== サムネイル用キャッチコピーを生成中 ===")
    thumbnail_copy = thumbnail_agent.generate(script_text)
    print(f"  main_text: {thumbnail_copy['main_text']}")
    print(f"  sub_text : {thumbnail_copy['sub_text']}")
    print(f"  visual_summary: {thumbnail_copy['visual_summary']}")

    thumbnail_path = None
    if generate_images and not args.no_gemini_fulltext:
        print("=== Geminiでサムネイルを丸ごと生成中 ===")
        thumbnail_path = generate_thumbnail_with_gemini(
            thumbnail_copy["main_text"],
            thumbnail_copy["sub_text"],
            output_path,
            visual_summary=thumbnail_copy["visual_summary"],
        )
        if thumbnail_path is None:
            print("  Geminiでの生成に失敗したため、Pillowでの組み立てにフォールバックします")

    if thumbnail_path is None:
        background_path = None
        if generate_images:
            print("=== 背景イラストを生成中(Gemini) ===")
            background_path = generate_slide_background(
                f"a wide 16:9 abstract illustration representing: {thumbnail_copy['main_text']}",
                output_path.parent / "thumbnail_background.png",
            )
        thumbnail_path = build_thumbnail(
            thumbnail_copy["main_text"],
            thumbnail_copy["sub_text"],
            output_path,
            background_path=background_path,
        )

    print(f"\n完了: {thumbnail_path}")


if __name__ == "__main__":
    main()
