"""台本(script.md)を構造化データに決定的にパースする。

動画組み立て(音声合成・字幕・スライド表示のタイミング)には「どのセリフが
どのシーンに属するか」を確実に知る必要がある。voicevox_agentのようにLLMで
再抽出する方式は柔軟だが、抽出内容が微妙にブレるリスクがある。
script.mdのフォーマットはscript_agentのプロンプトでこちらが完全に
コントロールしているため、ここは正規表現によるパースで確実性を優先する。

想定フォーマット(script_agentが一貫してこの形式で出力する前提):
  ### シーン<N>：<タイトル>（<開始時刻>〜<終了時刻>）
  つむぎ「セリフ」
  ずんだもん「セリフのだ」
  【画面：...】 (読み上げ対象外なので無視する)
"""

import re
from dataclasses import dataclass, field

SCENE_HEADER_RE = re.compile(r"^#{1,4}\s*シーン\s*(\d+)")
DIALOGUE_RE = re.compile(r"^(つむぎ|ずんだもん)「(.+)」\s*$")


@dataclass
class ScriptLine:
    """1セリフ分のデータ。"""

    speaker: str
    text: str
    scene_number: int


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
            speaker, text = dialogue_match.groups()
            current_scene.lines.append(
                ScriptLine(
                    speaker=speaker, text=text, scene_number=current_scene.number
                )
            )

    return scenes


def flatten_lines(scenes: list[Scene]) -> list[ScriptLine]:
    """全シーンのセリフを台本の登場順で1つのリストに平坦化する。"""
    lines: list[ScriptLine] = []
    for scene in scenes:
        lines.extend(scene.lines)
    return lines
