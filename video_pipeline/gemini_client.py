"""Gemini APIを呼び出す共通処理(JSON生成用)。

claude_client.pyのcall_json相当。総合エージェントのクロスチェック
(integration_agent.py)でのみ使う。画像生成用のGemini呼び出しは
image_generator.pyが別途独立して行っている。
"""

import json
import os

_client = None


def get_client():
    """Gemini(google-genai)クライアントをシングルトンで返す。

    GEMINI_API_KEY環境変数が未設定の場合は例外を投げる。呼び出し側で
    未設定時のフォールバック(Claude単独チェックへの切り替えなど)を行うこと。
    """
    global _client
    if _client is None:
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEYが未設定です")
        _client = genai.Client(api_key=api_key)
    return _client


def call_json(system: str, user: str, model: str) -> dict:
    """Geminiに『JSONのみ』を返すよう依頼し、パース済みdictを返す。

    response_mime_type="application/json"でJSON出力を強制するため、
    claude_client.call_jsonのようなコードフェンス除去・再試行は不要。
    """
    from google.genai import types

    response = get_client().models.generate_content(
        model=model,
        contents=[user],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)
