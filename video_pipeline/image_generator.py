"""Gemini API（Nano Banana系モデル）を使ってスライドの背景イラストを生成する。

注意: 画像生成モデルは文章・数値を正確に描画するのが苦手なため、背景画像には
文字を描かせず、実際のテキストは背景の上にPillowで正確に描画する
(slide_image_builder側)。背景プロンプト自体もslides_agent側で
「文字を描かせない」よう縛っている。

このモジュールはANTHROPIC_API_KEYではなくGEMINI_API_KEYを使う、
Claude側とは別のAPI呼び出しになる点に注意。
"""

import os
from pathlib import Path

from video_pipeline.config import GEMINI_IMAGE_MODEL

# 動画全体で背景の絵柄に統一感を持たせるため、slides_agentが書いた
# 内容依存のプロンプトに対し、常にこのスタイルを付け足す。
# (LLMの呼び出しごとに独立しているため、統一感の担保はコード側で行う)
BACKGROUND_STYLE_SUFFIX = (
    ", flat minimalist vector illustration, soft indigo and pastel blue color palette, "
    "clean modern tech/education aesthetic, plenty of empty negative space, "
    "no text, no numbers, no letters, no words"
)


def generate_slide_image(
    prompt: str,
    output_path: str | Path,
    aspect_ratio: str = "16:9",
    model: str | None = None,
) -> Path | None:
    """promptから画像を生成しoutput_pathに保存する。生成できなければNoneを返す。

    GEMINI_API_KEYが未設定、またはAPI呼び出しに失敗した場合は例外を投げず
    Noneを返す(背景生成はあくまでオプションなので、失敗してもパイプライン
    全体を止めない方針)。

    modelを指定しない場合はconfig.GEMINI_IMAGE_MODELを使う。サムネイルの
    ように画像内に正確な文字を焼き込みたい用途では、文字精度の高い
    (代わりに高価な)モデルを個別に指定できるようにしている。
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  [警告] GEMINI_API_KEYが未設定のため画像生成をスキップします")
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print(
            "  [警告] google-genaiがインストールされていないため画像生成をスキップします"
        )
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model or GEMINI_IMAGE_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["Text", "Image"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                output_path = Path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(part.inline_data.data)
                return output_path
    except Exception as exc:  # noqa: BLE001 画像生成の失敗で全体を止めたくないため広めに捕捉
        print(f"  [警告] 画像生成に失敗しました: {exc}")
        return None

    print("  [警告] 応答に画像データが含まれていませんでした")
    return None


def generate_slide_background(
    content_prompt: str, output_path: str | Path
) -> Path | None:
    """スライド背景を生成する。全スライド共通のスタイルを自動で付け足す。"""
    full_prompt = content_prompt.strip() + BACKGROUND_STYLE_SUFFIX
    return generate_slide_image(full_prompt, output_path, aspect_ratio="16:9")
