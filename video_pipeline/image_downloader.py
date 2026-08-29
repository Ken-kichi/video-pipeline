"""記事中の画像URL(![alt](https://...))から実際の画像をダウンロードする。

mermaid.ink(diagram_renderer)と同様、外部サーバー(記事のホスティング元)に
依存するため、ネットワークが使えない環境やURL切れで失敗することがある。
失敗してもパイプライン全体を止めず、その画像だけスキップする方針にする。
"""

from pathlib import Path

import requests


def download_image(url: str, output_path: str, timeout: int = 30):
    """URLから画像をダウンロードしてoutput_pathに保存する。

    失敗した場合は例外を投げずNoneを返す(呼び出し側でスキップできるように)。
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return output_path
    except Exception as exc:  # noqa: BLE001 失敗しても他のスライド生成を止めない
        print(f"  [警告] 画像のダウンロードに失敗しました: {exc}")
        return None
