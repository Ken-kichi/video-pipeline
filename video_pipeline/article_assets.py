"""記事(Markdown)からコードブロック・mermaid図を抽出する。

script_agent/slides_agentがセリフやスライドで記事中の図・コードに言及した際、
それを実際にスライドへ貼り付けられるように、事前に記事から抜き出して
インデックス付きで保持しておく。抽出はMarkdownのフェンス付きコードブロック
(```lang ... ```)を正規表現で決定的にパースするだけで、LLMには依存しない。
"""

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


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


def extract_article_assets(article_text: str) -> tuple[list[CodeBlock], list[DiagramBlock]]:
    """記事からコードブロックとmermaid図をそれぞれ抜き出す。

    mermaid図(```mermaid)はdiagramsに、それ以外の言語のコードブロックは
    codesに分けて格納する。それぞれ記事内での出現順に0始まりのindexを振る。
    """
    codes: list[CodeBlock] = []
    diagrams: list[DiagramBlock] = []

    current_heading = ""
    in_fence = False
    fence_lang = ""
    buffer: list[str] = []

    for raw_line in article_text.splitlines():
        if not in_fence:
            heading_match = _HEADING_RE.match(raw_line)
            if heading_match:
                current_heading = heading_match.group(2)
                continue

        fence_match = _FENCE_RE.match(raw_line.strip()) if raw_line.strip().startswith("```") else None

        if not in_fence and fence_match:
            in_fence = True
            fence_lang = fence_match.group(1).strip().lower()
            buffer = []
            continue

        if in_fence and raw_line.strip() == "```":
            in_fence = False
            code_text = "\n".join(buffer)
            if fence_lang == "mermaid":
                diagrams.append(
                    DiagramBlock(
                        index=len(diagrams), mermaid_source=code_text, heading_context=current_heading
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
            continue

        if in_fence:
            buffer.append(raw_line)

    return codes, diagrams


def summarize_for_prompt(codes: list[CodeBlock], diagrams: list[DiagramBlock], max_chars: int = 120) -> str:
    """slides_agentのプロンプトに埋め込む、コード/図の一覧サマリーを作る。"""
    lines: list[str] = []

    if codes:
        lines.append("## 記事中のコードブロック一覧(code_refで参照)")
        for c in codes:
            preview = c.code.strip().replace("\n", " ")[:max_chars]
            lines.append(f"- code_ref={c.index} [{c.language}] (「{c.heading_context}」節) {preview}...")

    if diagrams:
        lines.append("## 記事中のmermaid図一覧(diagram_refで参照)")
        for d in diagrams:
            preview = d.mermaid_source.strip().replace("\n", " ")[:max_chars]
            lines.append(f"- diagram_ref={d.index} (「{d.heading_context}」節) {preview}...")

    return "\n".join(lines)
