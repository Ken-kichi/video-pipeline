"""ファイル読み書きの共通処理。"""

from pathlib import Path


def read_markdown(path: str | Path) -> str:
    """入力のMarkdown記事を読み込む。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"記事ファイルが見つかりません: {p}")
    return p.read_text(encoding="utf-8")


def write_text_file(path: str | Path, content: str) -> Path:
    """テキストファイルを書き出す(親ディレクトリが無ければ作成)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p
