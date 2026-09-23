"""台本(script.md)を構造化データに決定的にパースする。

動画組み立て(音声合成・字幕・スライド表示のタイミング)には「どのセリフが
どのシーンに属するか」を確実に知る必要がある。voicevox_agentのようにLLMで
再抽出する方式は柔軟だが、抽出内容が微妙にブレるリスクがある。
script.mdのフォーマットはscript_agentのプロンプトでこちらが完全に
コントロールしているため、ここは正規表現によるパースで確実性を優先する。

想定フォーマット(script_agentが一貫してこの形式で出力する前提):
  ### シーン<N>：<タイトル>（<開始時刻>〜<終了時刻>）
  つむぎ「セリフ」
  ずんだもん（驚き）「セリフのだ」
  【画面：...】 (読み上げ対象外なので無視する)

セリフの感情表現(character_emotion_assets.py参照)のため、話者名の直後に
任意で（喜び|驚き|悲しみ|怒り）の感情タグを付けられる(省略時はneutral=
通常の立ち絵のまま)。render-videoの立ち絵オーバーレイが、このタグに応じて
喜怒哀楽の表情差分に一時的に切り替える。
"""

import re
from dataclasses import dataclass, field

SCENE_HEADER_RE = re.compile(r"^#{1,4}\s*シーン\s*(\d+)")
# 感情タグ部分は特定の4語(喜び|驚き|悲しみ|怒り)だけに絞らず、括弧内の
# 任意の文字列を緩くキャプチャする(全角/半角どちらの括弧にも対応)。
# script_agentのプロンプトはこの4語のみを使うよう指示しているが、LLMが
# 厳密に守るとは限らない(似た語「困惑」等を使う、半角括弧を使う、等の
# ブレが起こりうる)。以前は4語への完全一致を正規表現自体に組み込んで
# いたため、想定外の語が1つでも来るとタグ部分だけでなく行全体がマッチせず、
# そのセリフが警告無しで丸ごと消えてしまっていた。未知の語は下のEMOTION_LABELS
# 参照で"neutral"として扱われる(セリフ本文は必ず拾われる)。
DIALOGUE_RE = re.compile(r"^(つむぎ|ずんだもん)(?:[（(]([^）)]*)[）)])?「(.+)」\s*$")
# 話者名+「...」を含みそうだが、上のDIALOGUE_REにはマッチしなかった行を
# 検出するための緩い判定(警告表示用)。想定外のフォーマット崩れ
# (閉じ括弧の欠落等)でセリフが無警告のまま失われるのを防ぐ。
_POSSIBLE_DIALOGUE_RE = re.compile(r"^(つむぎ|ずんだもん).*「")

# 台本上の日本語の感情タグ語 -> character_emotion_assets.pyのファイル名に
# 使う英語キーへの対応。ここに無い語(LLMが4語以外を使った場合)は
# parse_script側でneutral扱いにフォールバックする(セリフ自体は失わない)。
EMOTION_LABELS = {"喜び": "happy", "驚き": "surprised", "悲しみ": "sad", "怒り": "angry"}


@dataclass
class ScriptLine:
    """1セリフ分のデータ。"""

    speaker: str
    text: str
    scene_number: int
    # "neutral" | "happy" | "surprised" | "sad" | "angry"
    emotion: str = "neutral"


@dataclass
class Scene:
    """1シーン分のデータ。"""

    number: int
    lines: list[ScriptLine] = field(default_factory=list)


def parse_script(script_text: str) -> list[Scene]:
    """台本テキストをシーンのリストにパースする。

    見出し(### シーン<N>：〜)が現れるたびに新しいシーンを開始し、
    その後に続く「つむぎ「〜」」「ずんだもん「〜」」の行をそのシーンの
    セリフとして集める。見出しより前に現れたセリフは無視する
    (通常は発生しない想定)。
    """
    scenes: list[Scene] = []
    current_scene: Scene | None = None

    for raw_line in script_text.splitlines():
        line = raw_line.strip()

        header_match = SCENE_HEADER_RE.match(line)
        if header_match:
            current_scene = Scene(number=int(header_match.group(1)))
            scenes.append(current_scene)
            continue

        dialogue_match = DIALOGUE_RE.match(line)
        if dialogue_match and current_scene is not None:
            speaker, emotion_label, text = dialogue_match.groups()
            emotion = EMOTION_LABELS.get(emotion_label, "neutral")
            current_scene.lines.append(
                ScriptLine(
                    speaker=speaker,
                    text=text,
                    scene_number=current_scene.number,
                    emotion=emotion,
                )
            )
        elif _POSSIBLE_DIALOGUE_RE.match(line):
            # セリフらしき行(話者名+「を含む)なのにDIALOGUE_REにマッチしな
            # かった場合、そのまま無視すると原因不明のままセリフが1行丸ごと
            # 動画から消えてしまう。フォーマット崩れの可能性が高いことを
            # 警告として出す(パース自体は継続する)。
            print(f"  [警告] script_parser: セリフらしき行を解析できませんでした: {line}")

    return scenes


def flatten_lines(scenes: list[Scene]) -> list[ScriptLine]:
    """全シーンのセリフを台本の登場順で1つのリストに平坦化する。"""
    lines: list[ScriptLine] = []
    for scene in scenes:
        lines.extend(scene.lines)
    return lines
