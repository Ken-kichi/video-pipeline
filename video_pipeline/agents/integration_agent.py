"""台本・スライド内容・VOICEVOX用テキストの3つを横断でチェックする総合エージェント。

各エージェントは自分の担当物しか見ていないため、
「台本にはあるのにスライドに反映されていない」「VOICEVOXテキストの話者順が
スライドの展開とズレている」といった横断的な不整合はここで検出する。
"""

import json

from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_EVALUATE

LABEL = "総合エージェント"

CHECK_SYSTEM = """あなたは動画制作パイプラインの最終チェック担当です。
台本・スライド内容・VOICEVOX用読み上げテキストの3つを横断的に確認し、
整合性を採点してください。

チェック観点:
- スライドの展開順が台本のシーン展開と一致しているか
- VOICEVOX用テキストのセリフ・話者・順番が台本と一致しているか
- 台本にある重要な内容（数値・図の説明など）がスライドから漏れていないか
- 3つのファイルを合わせて見たときに矛盾する記述がないか

採点基準:
- 100点: 完全に整合が取れている
- 70〜99点: 軽微な表現のズレ程度（許容範囲内〜軽度の指摘）
- 40〜69点: シーンの一部抜け落ちなど、視聴者が違和感を持ちうる問題がある
- 0〜39点: シーンが丸ごと欠落している、話者が入れ替わっているなど致命的な不整合がある

JSON形式のみを返してください:
{
  "score": <int 0-100>,
  "issues": ["<検出した問題点1>", "<問題点2>", ...],
  "script_feedback": "<台本側で直すべき点。無ければ空文字>",
  "slides_feedback": "<スライド側で直すべき点。無ければ空文字>",
  "voicevox_feedback": "<VOICEVOXテキスト側で直すべき点。無ければ空文字>"
}
"""


def check(script: str, slides: list[dict], voicevox_text: str) -> dict:
    slides_text = json.dumps({"slides": slides}, ensure_ascii=False, indent=2)
    user = (
        f"# 台本\n\n{script}\n\n"
        f"# スライド内容\n\n{slides_text}\n\n"
        f"# VOICEVOX用読み上げテキスト\n\n{voicevox_text}\n\n"
        "上記3つの整合性を確認してください。"
    )
    return call_json(CHECK_SYSTEM, user, model=MODEL_EVALUATE)
