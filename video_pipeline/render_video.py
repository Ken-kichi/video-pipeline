"""script.md・スライド画像・VOICEVOX音声から、字幕付き完成動画を組み立てるCLI。

事前にVOICEVOXアプリを起動しておくこと。ffmpegのインストールも必要
(Macなら `brew install ffmpeg`)。

使い方:
  uv run render-video --script output/20260731_153000/script.md \
    --slides-dir output/20260731_153000/slides \
    --output output/20260731_153000/final_video.mp4
"""

import argparse
from pathlib import Path

from video_pipeline.video_assembler import DEFAULT_BASE_URL, assemble_video


def main() -> None:
    parser = argparse.ArgumentParser(
        description="script.md・スライド・VOICEVOX音声から字幕付き動画を組み立てる"
    )
    parser.add_argument("--script", required=True, help="script.mdのパス")
    parser.add_argument(
        "--slides-dir", required=True, help="スライド画像ディレクトリ(manifest.jsonを含む)"
    )
    parser.add_argument("--output", required=True, help="出力する動画ファイルのパス(.mp4)")
    parser.add_argument(
        "--work-dir",
        default=None,
        help="音声・字幕などの中間ファイルの保存先(未指定なら出力先と同じ場所の_video_work/)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"VOICEVOX ENGINEのURL（デフォルト: {DEFAULT_BASE_URL}）",
    )
    args = parser.parse_args()

    script_path = Path(args.script)
    slides_dir = Path(args.slides_dir)
    if not script_path.exists():
        raise FileNotFoundError(f"台本ファイルが見つかりません: {script_path}")
    if not slides_dir.exists():
        raise FileNotFoundError(f"スライドディレクトリが見つかりません: {slides_dir}")

    try:
        assemble_video(
            script_path=script_path,
            slides_dir=slides_dir,
            output_path=args.output,
            work_dir=args.work_dir,
            base_url=args.base_url,
        )
    except Exception as exc:  # noqa: BLE001 CLIとして分かりやすいエラー表示にするため
        print(
            f"\n[エラー] 動画の組み立てに失敗しました: {exc}\n"
            "VOICEVOXアプリとffmpegが両方使える状態か確認してください。"
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
