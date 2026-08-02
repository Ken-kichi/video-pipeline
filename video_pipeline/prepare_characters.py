"""立ち絵素材(PSD)から、口を開いた/閉じた状態のPNGをvideo_pipeline/assets/characters/に
書き出す準備用CLI。render-videoはこのディレクトリに両方揃っているキャラクターだけ
自動でオーバーレイに使う。

配布されている「立ち絵素材」PSDは、パーツごとのレイヤーグループの中に複数の
バリエーションが並んでいる形式が多い(口グループの中に「わあ」「む」など)。
デフォルトの設定は、実際にアップロードされた2つのPSD
(春日部つむぎ・ずんだもんの配布素材)のレイヤー名に合わせている。
別のPSDや別のレイヤー名を使う場合は、各オプションで上書きできる。

使い方:
  uv run prepare-characters \
    --tsumugi-psd tsumugi.psd --zundamon-psd zundamon.psd

  # レイヤー名を変えたい場合
  uv run prepare-characters --tsumugi-psd tsumugi.psd \
    --tsumugi-mouth-group "!口" --tsumugi-mouth-closed "ほほえみ" --tsumugi-mouth-open "わあ"
"""

import argparse

from video_pipeline.character_renderer import render_character_states
from video_pipeline.video_assembler import CHARACTER_ASSETS_DIR, CHARACTER_PREFIXES

# 実際にアップロードされたPSD2種類のレイヤー名から決めたデフォルト値。
DEFAULT_MOUTH_GROUP = "!口"
DEFAULT_MOUTH_LAYERS = {
    "つむぎ": {"closed": "ほほえみ", "open": "わあ"},
    "ずんだもん": {"closed": "んー", "open": "ほあー"},
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="立ち絵PSDから口の開閉2状態のPNGをassets/characters/に書き出す"
    )
    parser.add_argument("--tsumugi-psd", default=None, help="つむぎの立ち絵素材PSDのパス")
    parser.add_argument("--zundamon-psd", default=None, help="ずんだもんの立ち絵素材PSDのパス")
    parser.add_argument(
        "--tsumugi-mouth-group", default=DEFAULT_MOUTH_GROUP, help="つむぎの口レイヤーグループ名"
    )
    parser.add_argument(
        "--zundamon-mouth-group", default=DEFAULT_MOUTH_GROUP, help="ずんだもんの口レイヤーグループ名"
    )
    parser.add_argument(
        "--tsumugi-mouth-closed",
        default=DEFAULT_MOUTH_LAYERS["つむぎ"]["closed"],
        help="つむぎの「口を閉じた」状態として使うレイヤー名",
    )
    parser.add_argument(
        "--tsumugi-mouth-open",
        default=DEFAULT_MOUTH_LAYERS["つむぎ"]["open"],
        help="つむぎの「口を開いた」状態として使うレイヤー名",
    )
    parser.add_argument(
        "--zundamon-mouth-closed",
        default=DEFAULT_MOUTH_LAYERS["ずんだもん"]["closed"],
        help="ずんだもんの「口を閉じた」状態として使うレイヤー名",
    )
    parser.add_argument(
        "--zundamon-mouth-open",
        default=DEFAULT_MOUTH_LAYERS["ずんだもん"]["open"],
        help="ずんだもんの「口を開いた」状態として使うレイヤー名",
    )
    args = parser.parse_args()

    jobs = [
        ("つむぎ", args.tsumugi_psd, args.tsumugi_mouth_group, args.tsumugi_mouth_closed, args.tsumugi_mouth_open),
        (
            "ずんだもん",
            args.zundamon_psd,
            args.zundamon_mouth_group,
            args.zundamon_mouth_closed,
            args.zundamon_mouth_open,
        ),
    ]

    did_any = False
    for speaker, psd_path, mouth_group, closed_name, open_name in jobs:
        if not psd_path:
            continue
        prefix = CHARACTER_PREFIXES[speaker]
        print(f"{speaker}: {psd_path} から口レイヤー「{mouth_group}」の"
              f"「{closed_name}」「{open_name}」を書き出します...")
        try:
            result = render_character_states(
                psd_path=psd_path,
                mouth_group_name=mouth_group,
                closed_layer_name=closed_name,
                open_layer_name=open_name,
                output_dir=CHARACTER_ASSETS_DIR,
            )
        except Exception as exc:  # noqa: BLE001 CLIとして分かりやすいエラー表示にするため
            print(f"[エラー] {speaker}の立ち絵書き出しに失敗しました: {exc}")
            raise SystemExit(1) from exc

        # render_character_statesは"closed.png"/"open.png"という固定名で書き出すため、
        # キャラクターごとに区別できるプレフィックス付きの名前にリネームする
        for state, path in result.items():
            renamed = CHARACTER_ASSETS_DIR / f"{prefix}_{state}.png"
            path.rename(renamed)
            print(f"  -> {renamed}")
        did_any = True

    if not did_any:
        raise SystemExit(
            "--tsumugi-psdまたは--zundamon-psdのどちらか(または両方)を指定してください。"
        )

    print(f"\n完了。{CHARACTER_ASSETS_DIR} に保存しました。"
          "render-video実行時に自動的に読み込まれます。")


if __name__ == "__main__":
    main()
