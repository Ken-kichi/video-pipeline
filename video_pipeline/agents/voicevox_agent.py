"""台本からVOICEVOXに読み込む用のテキストファイルを生成・評価・修正するエージェント。

出力フォーマットは「[話者] セリフ」の1行1セリフ形式。
台本のシーン見出しや【画面：〜】指示（読み上げ対象ではない）は含めない。
人間がVOICEVOX上で話者を切り替えながら上から順に読み込ませる想定。
"""

from video_pipeline.claude_client import call_text, call_json
from video_pipeline.config import MODEL_EVALUATE, MODEL_EXTRACT
from video_pipeline.loop import run_with_evaluation_loop

LABEL = "VOICEVOXテキストエージェント"

GENERATE_SYSTEM = """あなたは動画台本からVOICEVOX用の読み上げテキストを抽出する担当です。

制約:
- 台本中の「つむぎ「〜」」「ずんだもん「〜のだ」」のセリフだけを抽出する
- シーン見出し(### シーン〜)や【画面：〜】の画面指示は含めない
- 出力は1行につき1セリフ、"[話者名] セリフ本文" の形式（かぎ括弧は外す）
- セリフの順番は台本の登場順を維持する
- セリフの文言自体は台本から変更しない（読み上げ用の抽出のみ行う）
- 出力はこの形式のプレーンテキストのみ。前置きや説明文、コードフェンスは不要
"""

EVALUATE_SYSTEM = """あなたはVOICEVOX用テキストのレビュアーです。台本と読み上げ用テキストを
照らし合わせ、以下の観点で0〜100点の総合スコアとフィードバックを返してください。

評価観点:
- 台本中の全セリフが抜け漏れなく含まれているか
- 話者の割り当て（つむぎ/ずんだもん）が台本と一致しているか
- シーン見出しや画面指示など、読み上げるべきでない文言が混入していないか
- "[話者名] セリフ" の形式が全行で統一されているか

JSON形式 {"score": <int>, "feedback": "<改善点。問題なければ空文字>"} のみを返してください。
"""

REVISE_SYSTEM = """あなたはVOICEVOX用テキストの修正担当です。台本と現在の読み上げ用テキスト、
フィードバックをもとに修正してください。出力は "[話者名] セリフ" 形式のプレーンテキストのみ。
"""


def generate(script: str) -> str:
    user = f"# 動画台本\n\n{script}\n\n上記からVOICEVOX用の読み上げテキストを抽出してください。"
    return call_text(GENERATE_SYSTEM, user, model=MODEL_EXTRACT)


def evaluate(script: str, voicevox_text: str) -> dict:
    user = (
        f"# 動画台本\n\n{script}\n\n# 現在の読み上げ用テキスト\n\n{voicevox_text}\n\n"
        "上記を評価してください。"
    )
    return call_json(EVALUATE_SYSTEM, user, model=MODEL_EVALUATE)


def revise(script: str, voicevox_text: str, feedback: str) -> str:
    user = (
        f"# 動画台本\n\n{script}\n\n# 現在の読み上げ用テキスト\n\n{voicevox_text}\n\n"
        f"# フィードバック\n\n{feedback}\n\n上記フィードバックを反映して修正してください。"
    )
    return call_text(REVISE_SYSTEM, user, model=MODEL_EXTRACT)


def run(script: str) -> tuple[str, int, list[dict]]:
    """VOICEVOX用テキストの生成→評価→修正ループを実行する。"""
    return run_with_evaluation_loop(
        label=LABEL,
        generate=lambda: generate(script),
        evaluate=lambda text: evaluate(script, text),
        revise=lambda text, feedback: revise(script, text, feedback),
    )
