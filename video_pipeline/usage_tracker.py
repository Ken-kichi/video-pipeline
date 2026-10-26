"""Claude APIの使用量(トークン数)を、呼び出し元の関数ごとに集計する。

どの工程(エージェントの生成/評価/修正)がコストの大半を占めているかを、
Anthropic Consoleのモデル別グラフよりも細かく把握するためのもの。
呼び出し元の名前は呼び出しスタックから自動で取る(例: "script_agent.evaluate")ので、
各エージェント側のコードは変更しなくてよい。

金額は料金改定で変わるためここでは計算しない。代わりに「入力トークン換算」
(出力は入力の5倍、キャッシュ書き込みは1.25倍、キャッシュ読み取りは0.1倍の
料金という、Claudeの現行モデル共通の比率で入力トークン相当に揃えた値)を出す。
同じモデル同士ならこの値の比がそのままコストの比になる。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

_FIELDS = (
    "calls",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

# (model, label) -> 集計値
_totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
    lambda: {field: 0 for field in _FIELDS}
)


def _caller_label() -> str:
    """claude_client以外で最初の呼び出し元を"モジュール名.関数名"で返す。"""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") in (
        __name__,
        "video_pipeline.claude_client",
    ):
        frame = frame.f_back
    if frame is None:
        return "unknown"
    module = str(frame.f_globals.get("__name__", "?")).rsplit(".", 1)[-1]
    return f"{module}.{frame.f_code.co_name}"


def record(model: str, usage) -> None:
    """1回のAPI呼び出しの使用量を記録する(再試行も別の呼び出しとして数える)。"""
    entry = _totals[(model, _caller_label())]
    entry["calls"] += 1
    for field in _FIELDS[1:]:
        entry[field] += getattr(usage, field, 0) or 0


def reset() -> None:
    _totals.clear()


def _weighted(entry: dict[str, int]) -> float:
    return (
        entry["input_tokens"]
        + entry["output_tokens"] * 5
        + entry["cache_creation_input_tokens"] * 1.25
        + entry["cache_read_input_tokens"] * 0.1
    )


def snapshot() -> list[dict]:
    """入力換算トークンの多い順に、(モデル, 呼び出し元)ごとの集計を返す。"""
    rows = [
        {"model": model, "label": label, **entry, "weighted": round(_weighted(entry))}
        for (model, label), entry in _totals.items()
    ]
    return sorted(rows, key=lambda row: row["weighted"], reverse=True)


def format_summary() -> str:
    rows = snapshot()
    if not rows:
        return "Claude API呼び出しの記録はありません"
    total = sum(row["weighted"] for row in rows) or 1
    lines = [
        "Claude使用量(入力トークン換算の多い順。換算: 出力×5, キャッシュ書込×1.25, キャッシュ読取×0.1)",
        f"  {'呼び出し元':<28}{'モデル':<26}{'回数':>4}{'入力':>9}{'出力':>8}{'換算':>9}{'比率':>6}",
    ]
    for row in rows:
        lines.append(
            f"  {row['label']:<28}{row['model']:<26}{row['calls']:>4}"
            f"{row['input_tokens']:>9,}{row['output_tokens']:>8,}"
            f"{row['weighted']:>9,}{row['weighted'] / total:>6.0%}"
        )
    lines.append(f"  合計(入力換算): {total:,}")
    return "\n".join(lines)


def save(path: str | Path) -> Path:
    path = Path(path)
    path.write_text(
        json.dumps(snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
