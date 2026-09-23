"""記事中の画像URL(![alt](https://...))から実際の画像をダウンロードする。

mermaid.ink(diagram_renderer)と同様、外部サーバー(記事のホスティング元)に
依存するため、ネットワークが使えない環境やURL切れで失敗することがある。
失敗してもパイプライン全体を止めず、その画像だけスキップする方針にする。
"""

import io
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError


def download_image(url: str, output_path: str, timeout: int = 30):
    """URLから画像をダウンロードしてoutput_pathに保存する。

    失敗した場合は例外を投げずNoneを返す(呼び出し側でスキップできるように)。
    HTTPステータスが200でも、ホットリンク保護やURL失効でHTMLのエラーページ等が
    返ってくることがある(記事執筆時点では有効だったURLが動画生成時に切れている
    場合など)ため、Content-Typeだけでなく実際のバイト列がPillowで画像として
    開けるかまで検証してから保存する(でないと、破損した画像ファイルが
    後段のスライド生成でUnidentifiedImageErrorとしてクラッシュしてしまう)。
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if not content_type.split(";")[0].strip().lower().startswith("image/"):
            print(
                f"  [警告] 画像のダウンロードをスキップしました"
                f"(Content-Typeが画像ではありません: {content_type or '不明'}): {url}"
            )
            return None

        content = response.content
        try:
            with Image.open(io.BytesIO(content)) as img:
                img.verify()
        except UnidentifiedImageError:
            print(f"  [警告] 画像のダウンロードをスキップしました(画像として認識できません): {url}")
            return None

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        return output_path
    except Exception as exc:  # noqa: BLE001 失敗しても他のスライド生成を止めない
        print(f"  [警告] 画像のダウンロードに失敗しました: {exc}")
        return None
