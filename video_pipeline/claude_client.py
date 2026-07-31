"""Claude APIを呼び出す共通処理。

各エージェントはこのモジュールの関数経由でのみClaudeを呼び出す。
呼び出し方法を一箇所に集約しておくことで、モデル変更やリトライ処理の
修正が1箇所で済むようにしている。
"""

import json
import re

from anthropic import Anthropic

from video_pipeline.config import MAX_TOKENS

_client: Anthropic | None = None


def get_client() -> Anthropic:
    """Anthropicクライアントをシングルトンで返す。

    ANTHROPIC_API_KEY環境変数からAPIキーを読み込む(anthropicライブラリの標準動作)。
    """
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def call_text(system: str, user: str, model: str, max_tokens: int = MAX_TOKENS) -> str:
    """Claudeにテキスト生成を依頼し、テキスト本文を返す。

    modelは呼び出し側(各エージェント)が役割に応じて明示的に指定する
    (config.MODEL_GENERATE / MODEL_EVALUATE / MODEL_EXTRACT のいずれか)。
    """
    response = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _strip_code_fence(text: str) -> str:
    """```json ... ``` のようなコードフェンスを取り除く。"""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def call_json(
    system: str, user: str, model: str, max_tokens: int = MAX_TOKENS, retries: int = 2
) -> dict:
    """Claudeに『JSONのみ』を返すよう依頼し、パース済みdictを返す。

    パースに失敗した場合は、失敗した旨をClaudeに伝えて再試行する。
    """
    full_system = (
        system
        + "\n\n重要: 出力はJSONオブジェクトのみとすること。"
        "前置き・説明文・コードフェンス(```)は一切含めないこと。"
    )
    current_user = user
    last_raw = ""
    for attempt in range(retries + 1):
        raw = call_text(full_system, current_user, model=model, max_tokens=max_tokens)
        last_raw = raw
        cleaned = _strip_code_fence(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            if attempt == retries:
                break
            current_user = (
                f"{user}\n\n直前の出力はJSONとしてパースできませんでした:\n{raw}\n"
                "JSONオブジェクトのみを出力してください。"
            )
    raise ValueError(f"ClaudeからのJSON応答のパースに失敗しました。最終応答:\n{last_raw}")
