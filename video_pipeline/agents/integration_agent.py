"""台本・スライド内容・VOICEVOX用テキストの3つを横断でチェックする総合エージェント。

各エージェントは自分の担当物しか見ていないため、
「台本にはあるのにスライドに反映されていない」「VOICEVOXテキストの話者順が
スライドの展開とズレている」といった横断的な不整合はここで検出する。

Claude単独でのチェックだと、そのAI自身が見落としがちな不整合(表現の癖・
学習データの偏りに起因するもの)を拾えない懸念があるため、GEMINI_API_KEYが
設定されている場合は同じ観点(CHECK_SYSTEM)でGeminiにも独立にチェックさせる。
どちらか一方にしか出ない指摘は表現の揺れや誤検知であることが多く、これを
一律に修正ループのトリガーにすると些末な指摘のためだけに毎回
MAX_REVISION_LOOPS回ループしてしまうため、両方のAIが指摘した問題だけを
「確認済みissue」として修正ループに反映する(片方のみの指摘は
single_source_issuesとして参考表示に留め、ループの継続条件には使わない)。
GEMINI_API_KEY未設定時やGemini呼び出し失敗時はClaude単独のチェックに
フォールバックする。
"""

import json
import os

from video_pipeline import gemini_client
from video_pipeline.claude_client import call_json
from video_pipeline.config import MODEL_EVALUATE, MODEL_EVALUATE_GEMINI

LABEL = "総合エージェント"

CHECK_SYSTEM = """あなたは動画制作パイプラインの最終チェック担当です。
台本・スライド内容・VOICEVOX用読み上げテキストの3つを横断的に確認し、
整合性を採点してください。

チェック観点:
- スライドの展開順が台本のシーン展開と一致しているか
- VOICEVOX用テキストのセリフが、一人称・語尾・言い回しを含め台本と
  一言一句完全に一致しているか（要約・言い換え・表記の書き換えがないか）。
  特につむぎ「あーし」⇔ずんだもん「ボク」のような一人称の入れ替わりは
  致命的な不整合として重点的に確認する
- 台本にある重要な内容（数値・図の説明など）がスライドから漏れていないか
- 3つのファイルを合わせて見たときに矛盾する記述がないか

採点基準:
- 100点: 完全に整合が取れている
- 70〜99点: 軽微な表現のズレ程度（許容範囲内〜軽度の指摘）
- 40〜69点: シーンの一部抜け落ちなど、視聴者が違和感を持ちうる問題がある
- 0〜39点: シーンが丸ごと欠落している、話者が入れ替わっている、
  VOICEVOX用テキストで話者の一人称が入れ替わっているなど致命的な
  不整合がある

JSON形式のみを返してください:
{
  "score": <int 0-100>,
  "issues": ["<検出した問題点1>", "<問題点2>", ...],
  "script_feedback": "<台本側で直すべき点。無ければ空文字>",
  "slides_feedback": "<スライド側で直すべき点。無ければ空文字>",
  "voicevox_feedback": "<VOICEVOXテキスト側で直すべき点。無ければ空文字>"
}
"""

MERGE_SYSTEM = """あなたは、2つのAI(Claude・Gemini)が独立に行った台本・
スライド内容・VOICEVOX用テキストの整合性チェック結果を統合する担当です。

2つのAIのissuesを見比べ、同じ問題を指しているもの(表現は違っても指している
箇所・内容が同じもの)を「両方が指摘した問題」としてconfirmed_issuesにまとめ、
どちらか一方のAIしか指摘していない問題はsingle_source_issuesに分けてください。

採点基準(confirmed_issuesの内容を主な根拠にする。single_source_issuesは
参考程度に留め、大きく減点しない):
- 100点: 完全に整合が取れている
- 70〜99点: 軽微な表現のズレ程度（許容範囲内〜軽度の指摘）
- 40〜69点: シーンの一部抜け落ちなど、視聴者が違和感を持ちうる問題がある
- 0〜39点: シーンが丸ごと欠落している、話者が入れ替わっている、
  VOICEVOX用テキストで話者の一人称が入れ替わっているなど致命的な
  不整合がある

JSON形式のみを返してください:
{
  "score": <int 0-100>,
  "confirmed_issues": ["<両方のAIが指摘した問題1>", ...],
  "single_source_issues": ["<一方のAIのみが指摘した問題1>", ...],
  "script_feedback": "<confirmed_issuesに基づき台本側で直すべき点。無ければ空文字>",
  "slides_feedback": "<confirmed_issuesに基づきスライド側で直すべき点。無ければ空文字>",
  "voicevox_feedback": "<confirmed_issuesに基づきVOICEVOXテキスト側で直すべき点。無ければ空文字>"
}
"""


def _build_user(script: str, slides: list[dict], voicevox_text: str) -> str:
    slides_text = json.dumps({"slides": slides}, ensure_ascii=False, indent=2)
    return (
        f"# 台本\n\n{script}\n\n"
        f"# スライド内容\n\n{slides_text}\n\n"
        f"# VOICEVOX用読み上げテキスト\n\n{voicevox_text}\n\n"
        "上記3つの整合性を確認してください。"
    )


def _check_claude(script: str, slides: list[dict], voicevox_text: str) -> dict:
    user = _build_user(script, slides, voicevox_text)
    return call_json(CHECK_SYSTEM, user, model=MODEL_EVALUATE)


def _check_gemini(script: str, slides: list[dict], voicevox_text: str) -> dict:
    user = _build_user(script, slides, voicevox_text)
    return gemini_client.call_json(CHECK_SYSTEM, user, model=MODEL_EVALUATE_GEMINI)


def _merge(claude_result: dict, gemini_result: dict) -> dict:
    user = (
        "# ClaudeによるチェックA\n\n"
        f"{json.dumps(claude_result, ensure_ascii=False, indent=2)}\n\n"
        "# GeminiによるチェックB\n\n"
        f"{json.dumps(gemini_result, ensure_ascii=False, indent=2)}\n\n"
        "上記2つのAIによる独立したチェック結果を統合してください。"
    )
    merged = call_json(MERGE_SYSTEM, user, model=MODEL_EVALUATE)
    merged["issues"] = merged.pop("confirmed_issues", [])
    return merged


def check(script: str, slides: list[dict], voicevox_text: str) -> dict:
    """台本・スライド・VOICEVOXテキストの整合性をチェックする。

    返り値の"issues"は(Geminiクロスチェックが有効な場合)両方のAIが一致して
    指摘した問題のみ。片方のみの指摘は"single_source_issues"に入る
    (存在しない場合もある。呼び出し側は.get()で参照すること)。
    """
    claude_result = _check_claude(script, slides, voicevox_text)

    if not os.environ.get("GEMINI_API_KEY"):
        return claude_result

    try:
        gemini_result = _check_gemini(script, slides, voicevox_text)
    except Exception as exc:  # noqa: BLE001 Geminiの障害で品質ゲート自体を止めたくない
        print(
            f"  [{LABEL}] Geminiクロスチェックに失敗したためClaude単独の結果を使います: {exc}"
        )
        return claude_result

    return _merge(claude_result, gemini_result)
