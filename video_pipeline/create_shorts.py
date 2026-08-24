"""完成動画(final_video.mp4)からYouTubeショート(9:16)を切り出すCLI。

以前は動画冒頭をカットし、Canvaで縦長キャンバスの中央に貼り付け、
上下の空いたスペースに文言を入れる、という作業を手動で行っていた。
このコマンドはそれを自動化する。script_agentが台本の0:00〜1:00を
「単体でショートとして成立する概要パート」として生成する設計になっている
(8〜10個程度の短いシーンに分割される)ため、デフォルトでは台本の見出しから
概要パート最後のシーン番号を自動検出し、その終わりまでを切り出す
(--sceneで対象のシーン番号を明示的に変更可)。
切り出した映像は視聴維持率を意識して1.5倍速にする(本編には適用しない)。

`render-video`が書き出す`scene_boundaries.json`（同じディレクトリにある想定）
から、指定シーンの正確な終了時刻を読んで切り出す。見つからない場合
（古いバージョンで生成した動画など）は、--durationの目安秒数付近の
自然な切れ目(無音区間)を探すフォールバックになるが、BGMを使っていると
セリフ間の無音がかき消されて検出できないことがあるため、
scene_boundaries.jsonがある場合は必ずそちらが優先される。

呼び出すエージェントはshorts_agentのみ(単発のClaude API呼び出し1回。
サムネイル用とは別に、ショートに適したより強いフック文言を考える)。

縦長キャンバスは上段=フック文言/中段=スライド見出し・箇条書きの拡大表示/
下段=つむぎ・ずんだもんの立ち絵(本編と同じ口パク)の3段構成にする
(shorts_generator.py参照)。中段・下段の再構成にはrender-videoが書き出す
shorts_data.jsonが必要なため、古いバージョンで生成した動画はショート化
できない(render-videoを再実行してください)。

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
    find_overview_end_scene,
    read_scene_end_time,
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
        "--scene",
        type=int,
        default=None,
        help="このシーン番号の終わりまでを切り出す(省略時は台本から概要パートの"
        "最後のシーンを自動検出)。scene_boundaries.jsonが無い場合は使われない",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_SHORTS_DURATION_SECONDS,
        help=f"scene_boundaries.jsonが見つからない場合のフォールバック用の目安秒数"
        f"(デフォルト{DEFAULT_SHORTS_DURATION_SECONDS}秒。この付近の自然な切れ目に調整される)",
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

    script_text = script_path.read_text(encoding="utf-8")
    scene = args.scene if args.scene is not None else find_overview_end_scene(script_text)
    if args.scene is None:
        print(f"  台本から概要パートの最後のシーンを自動検出しました: シーン{scene}")

    scene_boundaries_path = video_path.parent / "scene_boundaries.json"
    exact_end_time = read_scene_end_time(scene_boundaries_path, scene)
    if exact_end_time is None:
        print(
            f"  [警告] {scene_boundaries_path} が見つからないか、"
            f"シーン{scene}の情報がありません。"
            f"目安{args.duration}秒付近の自然な切れ目で代用します"
            "(render-videoを最新版で再実行するとscene_boundaries.jsonが作られます)"
        )

    print("=== ショート用フック文言を生成中 ===")
    shorts_copy = shorts_agent.generate(script_text)
    print(f"  hook_text: {shorts_copy['hook_text']}")

    print("=== ショート動画を組み立て中 ===")
    try:
        result_path = build_shorts_video(
            source_video_path=video_path,
            output_path=output_path,
            hook_text=shorts_copy["hook_text"],
            duration=args.duration,
            exact_end_time=exact_end_time,
        )
    except FileNotFoundError as exc:
        print(f"\n[エラー] {exc}")
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001 CLIとして分かりやすいエラー表示にするため
        print(
            f"\n[エラー] ショート動画の組み立てに失敗しました: {exc}\nffmpegが使える状態か確認してください。"
        )
        raise SystemExit(1) from exc

    print(f"\n完了: {result_path}")


if __name__ == "__main__":
    main()
