"""ターミナル上で矢印キー選択できるインタラクティブメニューの共通処理。

--input や --script のようなパス指定を毎回タイプする代わりに、
`articles/*.md` や `output/*/` の候補を一覧から選べるようにする。

注意: questionaryはTTYが無い環境(パイプ実行時・CI等)では即座にNoneを
返さずハングすることがあるため、呼び出し前に sys.stdin.isatty() で
必ずガードする(実際にサンドボックス環境で確認済みの挙動)。
"""

import sys
from pathlib import Path

import questionary


def select_from_list(choices: list[str], message: str) -> str | None:
    """選択肢のリストから1つを矢印キーで選ばせる。

    TTYが無い(対話端末で実行されていない)場合や選択肢が無い場合はNoneを返す。
    選択肢が1つしか無ければ聞くまでもないのでそのまま返す。
    """
    if not choices:
        return None
    if len(choices) == 1:
        return choices[0]
    if not sys.stdin.isatty():
        return None
    return questionary.select(message, choices=choices).ask()


def confirm(message: str, default: bool = False) -> bool:
    """Yes/Noを矢印キー(またはy/n)で選ばせる。

    TTYが無い場合はdefaultをそのまま返す(プロンプト側の非対話フォールバックと
    同じ挙動にするため)。
    """
    if not sys.stdin.isatty():
        return default
    result = questionary.confirm(message, default=default).ask()
    return default if result is None else result


def text_input(message: str, default: str = "") -> str:
    """自由入力のテキストを1行受け取る。

    TTYが無い場合はdefaultをそのまま返す。
    """
    if not sys.stdin.isatty():
        return default
    result = questionary.text(message, default=default).ask()
    return default if result is None else result


def resolve_base_url(cli_value: str | None, default: str) -> str:
    """--base-urlが未指定なら、デフォルトのままか入力するかを対話的に選ばせる。"""
    if cli_value is not None:
        return cli_value
    choice = select_from_list(
        [f"デフォルト({default})を使う", "別のURLを指定する"],
        "VOICEVOX ENGINEのURLはどうしますか？",
    )
    if choice == "別のURLを指定する":
        return text_input("VOICEVOX ENGINEのURLを入力してください:", default=default)
    return default


def list_articles(articles_dir: str | Path = "articles") -> list[Path]:
    """記事ディレクトリ内のMarkdownファイル一覧を、更新が新しい順で返す。"""
    articles_dir = Path(articles_dir)
    if not articles_dir.exists():
        return []
    files = sorted(articles_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def pick_article(articles_dir: str | Path = "articles") -> Path | None:
    """記事ファイルをインタラクティブに選ばせる。"""
    files = list_articles(articles_dir)
    if not files:
        return None
    choice = select_from_list([f.name for f in files], "記事ファイルを選んでください:")
    if choice is None:
        return None
    return Path(articles_dir) / choice


def list_output_runs(output_base: str | Path = "output") -> list[Path]:
    """出力ディレクトリ(output/<実行時刻>/)の一覧を新しい順で返す。"""
    output_base = Path(output_base)
    if not output_base.exists():
        return []
    runs = [p for p in output_base.iterdir() if p.is_dir()]
    return sorted(runs, key=lambda p: p.name, reverse=True)


def _describe_run(run_dir: Path) -> str:
    """選択肢に表示するラベルを作る(ディレクトリ名+分かればタイトル)。"""
    script_path = run_dir / "script.md"
    title = ""
    if script_path.exists():
        for line in script_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("##"):
                title = stripped[2:].strip()
                break
    return f"{run_dir.name}  —  {title}" if title else run_dir.name


def pick_output_run(output_base: str | Path = "output") -> Path | None:
    """video-pipelineの出力ディレクトリ(output/<実行時刻>/)をインタラクティブに選ばせる。"""
    runs = list_output_runs(output_base)
    if not runs:
        return None
    labels = [_describe_run(r) for r in runs]
    choice = select_from_list(labels, "対象の出力ディレクトリを選んでください:")
    if choice is None:
        return None
    index = labels.index(choice)
    return runs[index]


_BGM_EXTENSIONS = (".mp3", ".wav", ".m4a")


def list_bgm_files(bgm_dir: str | Path) -> list[Path]:
    """BGMディレクトリ内の音声ファイル一覧を、名前順で返す。"""
    bgm_dir = Path(bgm_dir)
    if not bgm_dir.exists():
        return []
    files = [p for p in bgm_dir.iterdir() if p.suffix.lower() in _BGM_EXTENSIONS]
    return sorted(files, key=lambda p: p.name)


def pick_bgm_file(bgm_dir: str | Path) -> Path | None:
    """BGMファイルを対話的に選ばせる。「使わない」を選んだ場合や候補が無い場合はNone。"""
    bgm_dir = Path(bgm_dir)
    files = list_bgm_files(bgm_dir)
    if not files:
        return None

    no_bgm_label = "BGMを使わない"
    choices = [no_bgm_label] + [f.name for f in files]
    choice = select_from_list(choices, "BGMを選んでください:")
    if choice is None or choice == no_bgm_label:
        return None
    return bgm_dir / choice
