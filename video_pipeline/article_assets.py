"""記事(Markdown)からコードブロック・mermaid図・表を抽出する。

script_agent/slides_agentがセリフやスライドで記事中の図・コード・表に
言及した際、それを実際にスライドへ貼り付けられるように、事前に記事から
抜き出してインデックス付きで保持しておく。抽出はMarkdownのフェンス付き
コードブロック(```lang ... ```)とパイプ区切りの表(| ... | ... |)を
正規表現で決定的にパースするだけで、LLMには依存しない。
"""

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")


@dataclass
class CodeBlock:
    index: int
    language: str
    code: str
    heading_context: str  # 直前に出てきた見出し(どのセクションのコードか)


@dataclass
class DiagramBlock:
    index: int
    mermaid_source: str
    heading_context: str


@dataclass
class TableBlock:
    index: int
    header: list[str]
    rows: list[list[str]]
    heading_context: str  # 直前に出てきた見出し(どのセクションの表か)


_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BOLD_MARKER_RE = re.compile(r"\*\*(.+?)\*\*")


def _clean_cell_text(cell: str) -> str:
    """表示用にセルのMarkdown記法を軽く除去する(<br>を改行に、**太字**を除去)。"""
    cell = _BR_TAG_RE.sub("\n", cell)
    return _BOLD_MARKER_RE.sub(r"\1", cell)


def _split_table_row(line: str) -> list[str]:
    """`| a | b |`のような行をセルのリストに分割する(前後の空セルは除去)。"""
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [_clean_cell_text(cell.strip()) for cell in stripped.split("|")]


def _is_table_separator_row(line: str) -> bool:
    """`|---|---|`のような区切り行かどうかを判定する。"""
    if "|" not in line and "-" not in line:
        return False
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(_TABLE_SEPARATOR_CELL_RE.match(cell) for cell in cells)


def extract_article_assets(
    article_text: str,
) -> tuple[list[CodeBlock], list[DiagramBlock], list[TableBlock]]:
    """記事からコードブロック・mermaid図・表をそれぞれ抜き出す。

    mermaid図(```mermaid)はdiagramsに、それ以外の言語のコードブロックは
    codesに、Markdownのパイプ区切り表はtablesに分けて格納する。
    それぞれ記事内での出現順に0始まりのindexを振る。
    """
    codes: list[CodeBlock] = []
    diagrams: list[DiagramBlock] = []
    tables: list[TableBlock] = []

    current_heading = ""
    in_fence = False
    fence_lang = ""
    buffer: list[str] = []

    lines = article_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]

        if not in_fence:
            heading_match = _HEADING_RE.match(raw_line)
            if heading_match:
                current_heading = heading_match.group(2)
                i += 1
                continue

        fence_match = (
            _FENCE_RE.match(raw_line.strip())
            if raw_line.strip().startswith("```")
            else None
        )

        if not in_fence and fence_match:
            in_fence = True
            fence_lang = fence_match.group(1).strip().lower()
            buffer = []
            i += 1
            continue

        if in_fence and raw_line.strip() == "```":
            in_fence = False
            code_text = "\n".join(buffer)
            if fence_lang == "mermaid":
                diagrams.append(
                    DiagramBlock(
                        index=len(diagrams),
                        mermaid_source=code_text,
                        heading_context=current_heading,
                    )
                )
            else:
                codes.append(
                    CodeBlock(
                        index=len(codes),
                        language=fence_lang or "text",
                        code=code_text,
                        heading_context=current_heading,
                    )
                )
            i += 1
            continue

        if in_fence:
            buffer.append(raw_line)
            i += 1
            continue

        if (
            "|" in raw_line
            and raw_line.strip()
            and i + 1 < n
            and _is_table_separator_row(lines[i + 1])
        ):
            header = _split_table_row(raw_line)
            i += 2
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            tables.append(
                TableBlock(
                    index=len(tables),
                    header=header,
                    rows=rows,
                    heading_context=current_heading,
                )
            )
            continue

        i += 1

    return codes, diagrams, tables


def summarize_for_prompt(
    codes: list[CodeBlock],
    diagrams: list[DiagramBlock],
    tables: list[TableBlock] | None = None,
    max_chars: int = 120,
) -> str:
    """slides_agentのプロンプトに埋め込む、コード/図/表の一覧サマリーを作る。"""
    lines: list[str] = []

    if codes:
        lines.append("## 記事中のコードブロック一覧(code_refで参照)")
        for c in codes:
            preview = c.code.strip().replace("\n", " ")[:max_chars]
            lines.append(
                f"- code_ref={c.index} [{c.language}] (「{c.heading_context}」節) {preview}..."
            )

    if diagrams:
        lines.append("## 記事中のmermaid図一覧(diagram_refで参照)")
        for d in diagrams:
            preview = d.mermaid_source.strip().replace("\n", " ")[:max_chars]
            lines.append(
                f"- diagram_ref={d.index} (「{d.heading_context}」節) {preview}..."
            )

    if tables:
        lines.append("## 記事中の表一覧(table_refで参照)")
        for t in tables:
            preview = " / ".join(t.header)[:max_chars]
            lines.append(
                f"- table_ref={t.index} [{len(t.rows)}行] (「{t.heading_context}」節) "
                f"列: {preview}"
            )

    return "\n".join(lines)
