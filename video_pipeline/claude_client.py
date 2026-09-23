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

# 出力がmax_tokensで打ち切られた場合、この上限まで倍々に増やして再生成する。
# 非ストリーミングAPIはmax_tokensが21333を超えるとSDKがValueErrorを出すため、
# それより余裕を持たせた値にする(21333を超えたい場合はstream=Trueへの変更が必要)。
MAX_TOKENS_CEILING = 20000


def get_client() -> Anthropic:
    """Anthropicクライアントをシングルトンで返す。

    ANTHROPIC_API_KEY環境変数からAPIキーを読み込む(anthropicライブラリの標準動作)。
    """
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def _generate_with_truncation_retry(
    system: str, user: str, model: str, max_tokens: int, max_attempts: int = 3
) -> str:
    """max_tokens到達による出力の途中切れを検知し、上限を増やして再生成する。

    スライド数や台本が長くなるとJSON/テキストがmax_tokensに達して途中で
    切れることがある(スライドエージェントで実際に発生した不具合)。
    レスポンスのstop_reasonが"max_tokens"の場合、切り詰めずに上限を倍増して
    最初から生成し直す(部分的な継ぎ足しは行わない。JSON構造が壊れるため)。
    """
    current_max_tokens = max_tokens
    last_text = ""
    for attempt in range(max_attempts):
        response = get_client().messages.create(
            model=model,
            max_tokens=current_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        last_text = text
        if response.stop_reason != "max_tokens":
            return text

        if attempt < max_attempts - 1 and current_max_tokens < MAX_TOKENS_CEILING:
            current_max_tokens = min(current_max_tokens * 2, MAX_TOKENS_CEILING)
            print(
                f"  [警告] 出力がトークン上限で打ち切られました。"
                f"max_tokensを{current_max_tokens}に増やして再生成します"
            )
        else:
            print(
                f"  [警告] max_tokens={current_max_tokens}でも出力が打ち切られました。"
                "内容が不完全な可能性があります"
            )
            break

    return last_text


def call_text(system: str, user: str, model: str, max_tokens: int = MAX_TOKENS) -> str:
    """Claudeにテキスト生成を依頼し、テキスト本文を返す。

    modelは呼び出し側(各エージェント)が役割に応じて明示的に指定する
    (config.MODEL_GENERATE / MODEL_EVALUATE / MODEL_EXTRACT のいずれか)。
    """
    return _generate_with_truncation_retry(system, user, model, max_tokens)


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

    パースに失敗した場合(JSON構文として不正な場合はもちろん、構文的には
    正しいJSONでもトップレベルがdictでない場合も含む)、失敗した旨を
    Claudeに伝えて再試行する(max_tokens到達による打ち切りは
    _generate_with_truncation_retry側で別途トークン上限を増やして対処される)。
    """
    full_system = (
        system + "\n\n重要: 出力はJSONオブジェクトのみとすること。"
        "前置き・説明文・コードフェンス(```)は一切含めないこと。"
    )
    current_user = user
    last_raw = ""
    for attempt in range(retries + 1):
        raw = call_text(full_system, current_user, model=model, max_tokens=max_tokens)
        last_raw = raw
        cleaned = _strip_code_fence(raw)
        try:
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise json.JSONDecodeError(
                    f"expected a JSON object, got {type(parsed).__name__}",
                    cleaned,
                    0,
                )
            return parsed
        except json.JSONDecodeError:
            if attempt == retries:
                break
            current_user = (
                f"{user}\n\n直前の出力はJSONとしてパースできませんでした:\n{raw}\n"
                "JSONオブジェクトのみを出力してください。"
            )
    raise ValueError(
        f"ClaudeからのJSON応答のパースに失敗しました。最終応答:\n{last_raw}"
    )
