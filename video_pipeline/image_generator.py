"""Gemini API（Nano Banana系モデル）を使ってスライドの挿絵を生成する。

注意: 画像生成モデルは文章・数値を正確に描画するのが苦手なため、
image_promptには「文字を正確に描かせる」指示を含めない方針にしている
(slides_agent側のプロンプトで縛っている)。あくまで理解を助ける挿絵用途。

このモジュールはANTHROPIC_API_KEYではなくGEMINI_API_KEYを使う、
Claude側とは別のAPI呼び出しになる点に注意。
"""

import os
from pathlib import Path

from video_pipeline.config import GEMINI_IMAGE_MODEL


def generate_slide_image(prompt: str, output_path: str | Path) -> Path | None:
    """promptから画像を生成しoutput_pathに保存する。生成できなければNoneを返す。

    GEMINI_API_KEYが未設定、またはAPI呼び出しに失敗した場合は例外を投げず
    Noneを返す(挿絵はあくまでオプションなので、失敗してもパイプライン全体を
    止めない方針)。
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  [警告] GEMINI_API_KEYが未設定のため画像生成をスキップします")
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("  [警告] google-genaiがインストールされていないため画像生成をスキップします")
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(response_modalities=["Text", "Image"]),
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
