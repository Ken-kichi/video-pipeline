"""ローカルで起動しているVOICEVOX ENGINEのHTTP APIを使って音声合成する。

VOICEVOX(https://voicevox.hiroshiba.jp/)のデスクトップアプリを起動しておくと、
実体はHTTPサーバとして動作し(デフォルト http://127.0.0.1:50021)、
以下の2段階でテキストからWAV音声を生成できる:
  1. POST /audio_query?text=...&speaker=<id>  -> 音声合成用クエリ(JSON)を作成
  2. POST /synthesis?speaker=<id>              -> クエリを渡してWAV音声を合成

このモジュールはVOICEVOX ENGINEが別途起動していることを前提とする。
Claude/GeminiのAPIキーとは無関係の、完全にローカルな話者エンジンとの通信。
"""

import json
import re
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:50021"
DEFAULT_STYLE_NAME = "ノーマル"

# voicevox_agentが生成するテキストで実際に使われるキャラクター名。
# "[つむぎ] セリフ" のように角括弧付きになる場合と、"つむぎ セリフ" のように
# 角括弧なしになる場合の両方が実際に観測されているため、パーサーは両対応にする。
KNOWN_SPEAKER_NAMES = ["つむぎ", "ずんだもん"]


def list_speakers(base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    """起動中のVOICEVOX ENGINEから利用可能な話者一覧を取得する。"""
    response = requests.get(f"{base_url}/speakers", timeout=10)
    response.raise_for_status()
    return response.json()


def resolve_speaker_id(
    speakers: list[dict], character_name: str, style_name: str = DEFAULT_STYLE_NAME
) -> int:
    """話者名(例: "ずんだもん")とスタイル名(例: "ノーマル")からspeaker_idを引く。

    VOICEVOXに登録されている名前は「春日部つむぎ」のようにフルネームだが、
    台本上は「つむぎ」のように短縮した名前で呼んでいる場合があるため、
    完全一致が無ければ部分一致（どちらかがどちらかを含む）でも探す。
    指定したスタイルが見つからない場合は、その話者の最初のスタイルにフォールバックする。
    """
    matched_speaker = None
    for speaker in speakers:
        if speaker.get("name") == character_name:
            matched_speaker = speaker
            break

    if matched_speaker is None:
        for speaker in speakers:
            name = speaker.get("name", "")
            if character_name in name or name in character_name:
                matched_speaker = speaker
                break

    if matched_speaker is None:
        available = ", ".join(s.get("name", "?") for s in speakers)
        raise ValueError(
            f"話者「{character_name}」が見つかりません。"
            f"VOICEVOXに登録されている話者: {available}"
        )

    styles = matched_speaker.get("styles", [])
    for style in styles:
        if style.get("name") == style_name:
            return style["id"]
    if styles:
        return styles[0]["id"]
    raise ValueError(f"話者「{matched_speaker.get('name')}」にスタイルが登録されていません")


# 字幕には残したいが読み上げには不要な、非言語的な表現。カッコの中に
# これらの語だけが入っている場合に取り除く(例: 「（笑）」がそのまま
# 音声合成され「わらい」と読まれてしまう不具合の対策)。
_NON_VERBAL_PATTERN = re.compile(r"[（(](笑|苦笑|汗|涙|驚き|ため息)[）)]")

# 読み間違いが分かっている単語の読み補正。キーは元のテキスト、値は読み上げ用に
# 差し替えるかな表記。字幕には影響しない(音声合成に渡す直前だけ差し替える)。
# 例: 「空の箱」の「空」がデフォルトで「そら」と読まれ「そらの箱」に
# なってしまう不具合の対策(正しくは「からの箱」)。
READING_OVERRIDES: dict[str, str] = {
    "空の": "からの",
}


def _prepare_synthesis_text(text: str) -> str:
    """音声合成に渡す直前のテキスト整形(字幕表示用のtextとは別に扱う)。"""
    cleaned = _NON_VERBAL_PATTERN.sub("", text)
    for original, reading in READING_OVERRIDES.items():
        cleaned = cleaned.replace(original, reading)
    return cleaned


def synthesize(text: str, speaker_id: int, base_url: str = DEFAULT_BASE_URL) -> bytes:
    """1行分のテキストをWAV音声(bytes)に変換する。

    textには字幕用の元の表記(「（笑）」や「空の箱」等)がそのまま渡ってきて構わない。
    音声合成にだけ影響する整形(_prepare_synthesis_text)をここで適用する。
    """
    synthesis_text = _prepare_synthesis_text(text)
    query_response = requests.post(
        f"{base_url}/audio_query",
        params={"text": synthesis_text, "speaker": speaker_id},
        timeout=30,
    )
    query_response.raise_for_status()

    synthesis_response = requests.post(
        f"{base_url}/synthesis",
        params={"speaker": speaker_id},
        headers={"Content-Type": "application/json"},
        data=json.dumps(query_response.json()),
        timeout=60,
    )
    synthesis_response.raise_for_status()
    return synthesis_response.content


def parse_voicevox_script(
    text: str, speaker_names: list[str] | None = None
) -> list[tuple[str, str]]:
    """VOICEVOX用テキストを (話者名, セリフ) のリストに分解する。

    "[話者名] セリフ" (角括弧あり) と "話者名 セリフ" (角括弧なし) の
    どちらの形式にも対応する。話者名は既知のキャラクター名のみを対象にする
    (自由な正規表現だと本文中の記号を誤って話者名と誤認するリスクがあるため)。
    """
    speaker_names = speaker_names or KNOWN_SPEAKER_NAMES
    escaped_names = "|".join(re.escape(name) for name in speaker_names)
    pattern = re.compile(rf"^\s*\[?({escaped_names})\]?[:\s]+(.+?)\s*$")

    lines: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        match = pattern.match(raw_line)
        if match:
            speaker_name, line_text = match.groups()
            lines.append((speaker_name, line_text))
    return lines


def synthesize_script_file(
    voicevox_text: str,
    output_dir: str | Path,
    style_map: dict[str, str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[Path]:
    """voicevox_script.txtの内容から、1セリフ=1WAVファイルとして書き出す。

    ファイル名は "001_つむぎ.wav" のように連番+話者名にし、DaVinci Resolveの
    タイムラインに上から順に並べやすくしている。あわせてmanifest.jsonに
    各ファイルと対応するテキストの一覧を書き出す。
    """
    style_map = style_map or {}
    speakers = list_speakers(base_url)
    speaker_id_cache: dict[str, int] = {}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = parse_voicevox_script(voicevox_text)
    if not lines:
        raise ValueError(
            "voicevox_script.txtからセリフを1行も抽出できませんでした。"
            f"既知の話者名{KNOWN_SPEAKER_NAMES}で始まる行があるか確認してください。"
        )

    paths: list[Path] = []
    manifest: list[dict] = []
    for i, (character_name, line_text) in enumerate(lines, start=1):
        if character_name not in speaker_id_cache:
            style_name = style_map.get(character_name, DEFAULT_STYLE_NAME)
            speaker_id_cache[character_name] = resolve_speaker_id(
                speakers, character_name, style_name
            )
        speaker_id = speaker_id_cache[character_name]

        print(f"  {i:03d}: [{character_name}] {line_text[:30]}...")
        wav_bytes = synthesize(line_text, speaker_id, base_url)

        path = output_dir / f"{i:03d}_{character_name}.wav"
        path.write_bytes(wav_bytes)
        paths.append(path)
        manifest.append(
            {"index": i, "speaker": character_name, "text": line_text, "file": path.name}
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return paths
