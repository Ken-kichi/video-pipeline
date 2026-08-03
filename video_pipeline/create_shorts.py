"""完成動画(final_video.mp4)からYouTubeショート(9:16)を切り出すCLI。

以前は動画冒頭をカットし、Canvaで縦長キャンバスの中央に貼り付け、
上下の空いたスペースに文言を入れる、という作業を手動で行っていた。
このコマンドはそれを自動化する。script_agentが台本の0:00〜1:00を
「単体でショートとして成立する概要パート」として生成する設計になっている
ため、デフォルトでは冒頭60秒を切り出す(--durationで変更可。指定秒数
ぴったりで切るとセリフの途中で途切れるため、実際には指定秒数付近の
自然な切れ目に合わせて調整される)。

呼び出すエージェントはshorts_agentのみ(単発のClaude API呼び出し1回。
サムネイル用とは別に、ショートに適したより強いフック文言を考える)。

使い方:
  uv run create-shorts --video output/20260731_153000/final_video.mp4
  uv run create-shorts    # --videoを省略するとoutput/*/から対話的に選べる
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

from video_pipeline.agents import shorts_agent
from video_pipeline.interactive import pick_output_run
from video_pipeline.shorts_generator import (
    DEFAULT_SHORTS_DURATION_SECONDS,
    build_shorts_video,
)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="完成動画からYouTubeショート(9:16)を切り出す"
    )
    parser.add_argument(
        "--video",
        default=None,
        help="元になる完成動画(final_video.mp4)のパス。省略するとoutput/*/から対話的に選べる",
    )
    parser.add_argument(
        "--script",
        default=None,
        help="フック文言生成に使うscript.mdのパス(省略時は動画と同じディレクトリのもの)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="出力するショート動画のパス(省略時は動画と同じディレクトリのshorts.mp4)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_SHORTS_DURATION_SECONDS,
        help=f"冒頭から切り出す目安秒数(デフォルト{DEFAULT_SHORTS_DURATION_SECONDS}秒。"
        "台本の概要パート(0:00〜1:00)の実際の長さに合わせて調整してよい。"
        "実際の切り出し秒数はこの付近の自然な切れ目に調整される)",
    )
    args = parser.parse_args()

    video_path = Path(args.video) if args.video else None
    if video_path is None:
        run_dir = pick_output_run()
        if run_dir is None:
            raise SystemExit(
                "対象の動画が指定されていません。--videoで指定するか、"
                "output/ディレクトリにfinal_video.mp4がある状態で対話端末から実行してください。"
            )
        print(f"選択された実行結果: {run_dir.name}")
        video_path = run_dir / "final_video.mp4"

    if not video_path.exists():
        raise FileNotFoundError(
            f"動画ファイルが見つかりません: {video_path}\n"
            "先に render-video で final_video.mp4 を生成してください。"
        )

    script_path = Path(args.script) if args.script else video_path.parent / "script.md"
    if not script_path.exists():
        raise FileNotFoundError(f"台本ファイルが見つかりません: {script_path}")

    output_path = Path(args.output) if args.output else video_path.parent / "shorts.mp4"

    print("=== ショート用フック文言を生成中 ===")
    script_text = script_path.read_text(encoding="utf-8")
    shorts_copy = shorts_agent.generate(script_text)
    print(f"  hook_text  : {shorts_copy['hook_text']}")
    print(f"  follow_text: {shorts_copy['follow_text']}")

    print(f"=== ショート動画を組み立て中(冒頭{args.duration}秒付近) ===")
    try:
        result_path = build_shorts_video(
            source_video_path=video_path,
            output_path=output_path,
            main_text=shorts_copy["hook_text"],
            sub_text=shorts_copy["follow_text"],
            duration=args.duration,
        )
    except Exception as exc:  # noqa: BLE001 CLIとして分かりやすいエラー表示にするため
        print(
            f"\n[エラー] ショート動画の組み立てに失敗しました: {exc}\nffmpegが使える状態か確認してください。"
        )
        raise SystemExit(1) from exc

    print(f"\n完了: {result_path}")


if __name__ == "__main__":
    main()
