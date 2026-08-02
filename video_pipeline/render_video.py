"""script.md・スライド画像・VOICEVOX音声から、字幕付き完成動画を組み立てるCLI。

事前にVOICEVOXアプリを起動しておくこと。ffmpegのインストールも必要
(Macなら `brew install ffmpeg-full`)。

使い方:
  uv run render-video --script output/20260731_153000/script.md \
    --slides-dir output/20260731_153000/slides \
    --output output/20260731_153000/final_video.mp4

  # または、output/*/ から対話的に1回選ぶだけで上記3つが自動的に決まる
  uv run render-video
"""

import argparse
from pathlib import Path

from video_pipeline.interactive import pick_output_run, resolve_base_url
from video_pipeline.video_assembler import DEFAULT_BASE_URL, assemble_video


def main() -> None:
    parser = argparse.ArgumentParser(
        description="script.md・スライド・VOICEVOX音声から字幕付き動画を組み立てる"
    )
    parser.add_argument(
        "--script", default=None, help="script.mdのパス(省略時は対話選択したディレクトリ内のもの)"
    )
    parser.add_argument(
        "--slides-dir",
        default=None,
        help="スライド画像ディレクトリ(manifest.jsonを含む。省略時は対話選択したディレクトリ内のもの)",
    )
    parser.add_argument(
        "--output", default=None, help="出力する動画ファイルのパス(.mp4、省略時は対話選択したディレクトリ内)"
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="音声・字幕などの中間ファイルの保存先(未指定なら出力先と同じ場所の_video_work/)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"VOICEVOX ENGINEのURL。未指定なら対話的に選べる（デフォルト: {DEFAULT_BASE_URL}）",
    )
    args = parser.parse_args()

    # --script/--slides-dir/--outputのいずれかが未指定なら、output/*/を1回選んで
    # 3つとも自動的に組み立てる(毎回同じディレクトリ名を3回打つ手間を無くすため)
    if args.script is None or args.slides_dir is None or args.output is None:
        run_dir = pick_output_run()
        if run_dir is None:
            raise SystemExit(
                "対象のディレクトリが指定されていません。--script/--slides-dir/--outputを"
                "個別に指定するか、output/ディレクトリに実行結果がある状態で"
                "対話端末から実行してください。"
            )
        print(f"選択された実行結果: {run_dir.name}")
        script_path = Path(args.script) if args.script else run_dir / "script.md"
        slides_dir = Path(args.slides_dir) if args.slides_dir else run_dir / "slides"
        output_path = args.output if args.output else str(run_dir / "final_video.mp4")
    else:
        script_path = Path(args.script)
        slides_dir = Path(args.slides_dir)
        output_path = args.output

    if not script_path.exists():
        raise FileNotFoundError(f"台本ファイルが見つかりません: {script_path}")
    if not slides_dir.exists():
        raise FileNotFoundError(f"スライドディレクトリが見つかりません: {slides_dir}")

    base_url = resolve_base_url(args.base_url, DEFAULT_BASE_URL)

    try:
        assemble_video(
            script_path=script_path,
            slides_dir=slides_dir,
            output_path=output_path,
            work_dir=args.work_dir,
            base_url=base_url,
        )
    except Exception as exc:  # noqa: BLE001 CLIとして分かりやすいエラー表示にするため
        print(
            f"\n[エラー] 動画の組み立てに失敗しました: {exc}\n"
            "VOICEVOXアプリとffmpegが両方使える状態か確認してください。"
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
