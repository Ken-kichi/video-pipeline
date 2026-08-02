"""mermaid.ink(公開サービス)を使ってmermaid図をPNG画像として取得する。

mermaid.ink(https://mermaid.ink)は、mermaid記法のテキストをURLに
base64url埋め込みするだけで画像を返してくれる公開サービス。
ローカルにNode.js/ブラウザ等を用意しなくて済む。

注意: 外部の公開サービスに依存するため、ネットワークが使えない環境や
サービス側の問題で失敗することがある。失敗してもパイプライン全体を
止めず、その図だけスキップする方針にする(Gemini背景生成と同じ方針)。
"""

import base64

import requests

MERMAID_INK_BASE_URL = "https://mermaid.ink/img"


def render_mermaid_diagram(mermaid_source: str, output_path: str, timeout: int = 30):
    """mermaid記法のテキストをmermaid.inkに送り、PNG画像として保存する。

    失敗した場合は例外を投げずNoneを返す(呼び出し側でスキップできるように)。
    """
    from pathlib import Path

    try:
        encoded = base64.urlsafe_b64encode(mermaid_source.encode("utf-8")).decode("ascii")
        url = f"{MERMAID_INK_BASE_URL}/{encoded}?bgColor=white"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return output_path
    except Exception as exc:  # noqa: BLE001 失敗しても他のスライド生成を止めない
        print(f"  [警告] mermaid図のレンダリングに失敗しました: {exc}")
        return None
