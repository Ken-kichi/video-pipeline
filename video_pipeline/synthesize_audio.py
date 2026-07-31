"""VOICEVOX ENGINE(ローカル起動)を使って、voicevox_script.txtから音声を一括生成するCLI。

事前にVOICEVOXアプリを起動しておくこと。アプリの実体はHTTPサーバとして動作し、
デフォルトで http://127.0.0.1:50021 で待ち受ける。

使い方:
  uv run voicevox-synthesize --input output/20260731_153000/voicevox_script.txt
"""

import argparse
from pathlib import Path

from video_pipeline.voicevox_client import DEFAULT_BASE_URL, synthesize_script_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VOICEVOX ENGINE(ローカル)でvoicevox_script.txtから音声を一括生成する"
    )
    parser.add_argument("--input", required=True, help="voicevox_script.txtのパス")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="出力先ディレクトリ（未指定なら入力ファイルと同じ場所の audio/ ）",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"VOICEVOX ENGINEのURL（デフォルト: {DEFAULT_BASE_URL}）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_path}")

    voicevox_text = input_path.read_text(encoding="utf-8")
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent / "audio"

    print(f"VOICEVOX ENGINE ({args.base_url}) に接続して音声を生成します...")
    try:
        paths = synthesize_script_file(voicevox_text, output_dir, base_url=args.base_url)
    except Exception as exc:  # noqa: BLE001 CLIとして分かりやすいエラー表示にするため
        print(
            f"\n[エラー] 音声生成に失敗しました: {exc}\n"
            "VOICEVOXアプリが起動しているか、URLが正しいか確認してください。"
        )
        raise SystemExit(1) from exc

    print(f"\n完了: {len(paths)}個の音声ファイルを {output_dir} に保存しました")
    print(f"一覧: {output_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
