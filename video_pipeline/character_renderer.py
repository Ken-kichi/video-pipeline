"""VOICEVOX立ち絵素材(PSD)から、口を開いた状態/閉じた状態の透過PNGを生成する。

配布されている「立ち絵素材」PSDは、パーツごとにレイヤーグループが分かれており
(「!口」グループの中に複数の口の形が「*」付きレイヤーとして並んでいる、など)、
表示したいレイヤーだけvisible=Trueにしてpsd.composite()すると、その組み合わせの
1枚絵が合成できる。ここでは「口」グループだけ差し替えて、mouth_closed/
mouth_openの2状態を透過PNGとして書き出す(他のパーツはPSDの初期表示状態のまま)。
"""

from pathlib import Path

from PIL import Image as PILImage
from psd_tools import PSDImage


def _find_group(layers, name: str):
    """レイヤーツリーを再帰的に探索し、指定した名前のグループを返す。"""
    for layer in layers:
        if layer.name == name:
            return layer
        if layer.is_group():
            found = _find_group(layer, name)
            if found is not None:
                return found
    return None


def render_character_states(
    psd_path: str | Path,
    mouth_group_name: str,
    closed_layer_name: str,
    open_layer_name: str,
    output_dir: str | Path,
    crop_top_ratio: float = 0.48,
    display_height: int = 480,
) -> dict[str, Path]:
    """PSDから口を閉じた/開いた状態の2枚を合成し、透過PNGとして保存する。

    Args:
        psd_path: 立ち絵素材PSDのパス
        mouth_group_name: 口のレイヤーグループ名(例: "!口")
        closed_layer_name: 閉じた口として使うレイヤー名("*"は付けなくてよい)
        open_layer_name: 開いた口として使うレイヤー名
        output_dir: 出力先ディレクトリ
        crop_top_ratio: キャンバス上部から何割を切り出すか(立ち絵は全身縦長のため、
            バストアップ程度に絞る)
        display_height: 出力するPNGの高さ(px)。アスペクト比を保って幅は自動計算

    Returns:
        {"closed": Path, "open": Path}
    """
    psd = PSDImage.open(str(psd_path))
    mouth_group = _find_group(psd, mouth_group_name)
    if mouth_group is None:
        raise ValueError(f"レイヤーグループ「{mouth_group_name}」が見つかりません")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}
    for state, layer_name in (("closed", closed_layer_name), ("open", open_layer_name)):
        matched = False
        for layer in mouth_group:
            is_target = layer.name.strip("*") == layer_name
            layer.visible = is_target
            matched = matched or is_target
        if not matched:
            raise ValueError(
                f"口レイヤー「{layer_name}」が見つかりません。"
                f"候補: {[l.name for l in mouth_group]}"
            )

        image = psd.composite()
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        w, h = image.size
        cropped = image.crop((0, 0, w, int(h * crop_top_ratio)))
        scale = display_height / cropped.height
        new_size = (max(1, int(cropped.width * scale)), display_height)
        resized = cropped.resize(new_size, PILImage.LANCZOS)

        path = output_dir / f"{state}.png"
        resized.save(path)
        results[state] = path

    return results
