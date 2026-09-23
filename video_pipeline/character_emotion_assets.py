"""感情差分の立ち絵(喜怒哀楽)を、Geminiで一度だけ生成してキャッシュする。

`assets/characters/`には口の開閉2状態(closed/open)の「通常」立ち絵しか
無く、動画中はずっとこの表情のまま口パクするだけだった。台本エージェント
(script_agent.py)がセリフごとに感情タグ(script_parser.pyの（喜び）
（驚き）（悲しみ）（怒り）)を付けられるようになったことに合わせ、
非neutralな感情のセリフが流れている間だけ、その感情に描き直した立ち絵に
切り替えられるようにする。

生成した画像は`assets/characters/`に`{prefix}_{emotion}_{state}.png`
として保存し、2回目以降はGeminiを呼ばずキャッシュを再利用する(動画ごとに
生成すると絵柄がカットごとに微妙に揺れるうえ、コスト・生成時間もかかる
ため。「一度だけ生成してキャッシュする」方針は姉妹プロジェクト
(code-video-toolkit-app)と同じ)。GEMINI_API_KEY未設定・生成失敗時は
Noneを返し、呼び出し側(video_assembler.py/shorts_generator.py)は該当
区間だけ通常表情のままフォールバックする(表情差し替えはあくまで演出上の
おまけであり、失敗しても動画生成全体を止めない)。

立ち絵はcode-server画面や動画背景の上に透過合成する前提の素材のため、
単純な画像生成では得られない透過(アルファチャンネル)が必要になる。
Nano Banana系モデルは透過PNGを直接出力できないため、背景を単色の
クロマキー色で塗らせるようプロンプトで指示し、生成後にPillowで
その色を透過に変換する(グリーンバックの要領。ずんだもんは髪が緑色の
ため、緑ではなく両キャラクターの配色と被らないマゼンタをキー色にする)。
"""

from pathlib import Path

from PIL import Image, ImageChops

from video_pipeline.image_generator import generate_slide_image

CHARACTER_ASSETS_DIR = Path(__file__).parent / "assets" / "characters"

# つむぎ(金髪)・ずんだもん(緑髪)どちらの配色とも被らない、彩度の高い
# マゼンタをクロマキー色にする(緑にすると、ずんだもんの緑髪ごと
# 透過されてしまうため使えない)。
_CHROMA_KEY_RGB = (255, 0, 255)
# キー色との色距離がこの値以下なら完全に透過、この値の2倍以上なら完全に
# 不透明にする(間は線形にフェードさせ、縁のジャギーを抑える)。
_CHROMA_KEY_TOLERANCE = 60

# 【重要】ここでは口の形(開いている/閉じている)に一切言及しない。
# 以前"happy"に"big open smile"と書いていたところ、closed(口を閉じた)
# 状態の参照画像を渡してもGeminiがこの文言を優先して口を開けて描いて
# しまい、ファイル名(_closed)と実際の見た目(口が開いている)が食い違う
# 不具合が実機(code-video-toolkit-app側)で確認された。口の開閉は
# `_build_prompt`側で参照画像の状態をそのまま維持するよう別途明示的に
# 指示し、ここでは目・眉・頬・姿勢など口以外の要素だけを記述する。
_EMOTION_DESCRIPTIONS = {
    "happy": (
        "eyes closed in a joyful crescent shape or sparkling with happiness, "
        "rosy blushing cheeks, sparkle effects near the face, a cheerful "
        "upward tilt of the head, a little bounce in posture"
    ),
    "surprised": (
        "wide-eyed shock, eyebrows raised high, leaning back slightly as if "
        "startled, one hand near the face"
    ),
    "sad": (
        "downcast eyes, drooping eyebrows, shoulders slumped, "
        "a single sweat drop or teardrop near the face is okay"
    ),
    "angry": (
        "furrowed brow, puffed-up cheeks, small clenched fists held at "
        "chest height, indignant posture"
    ),
}

_EN_NAMES = {"tsumugi": "Tsumugi", "zundamon": "Zundamon"}

# 生成結果の四隅の小さな正方形パッチをスキャンして、実際にクロマキー色の
# 背景になっているかを確認する際のパッチサイズ・許容距離・パッチ内で
# 「キー色に近い」とみなす画素の割合・最大リトライ回数。プロンプトで
# 指示していても、Geminiが背景指示を無視して白背景で生成したり、無関係な
# 図形を背景の一部に描き足したりすることが実際にあった(code-video-toolkit-app
# 側で実機確認)。四隅の1点だけのサンプルや外周全体の1px幅スキャンは
# それぞれ見逃し・誤検知が多かったため、4隅それぞれの小さな正方形パッチ
# (内部に立ち絵本体が到達することはほぼ無い領域)の一致率で判定する。
_CORNER_PATCH_SIZE = 80
_BACKGROUND_VALID_DISTANCE = _CHROMA_KEY_TOLERANCE * 2
_CORNER_VALID_RATIO = 0.85
_MAX_GENERATION_ATTEMPTS = 3


def _has_chroma_key_background(img: Image.Image) -> bool:
    """画像の4隅それぞれの小さな正方形パッチが、大部分クロマキー色に
    近いかを確認する。4隅すべてが基準を満たして初めてTrueを返す。"""
    rgb = img.convert("RGB")
    w, h = rgb.size
    patch = min(_CORNER_PATCH_SIZE, w // 4, h // 4)
    kr, kg, kb = _CHROMA_KEY_RGB
    corners = ((0, 0), (w - patch, 0), (0, h - patch), (w - patch, h - patch))

    for cx, cy in corners:
        total = 0
        near = 0
        for x in range(cx, cx + patch, 4):
            for y in range(cy, cy + patch, 4):
                r, g, b = rgb.getpixel((x, y))
                dist = ((r - kr) ** 2 + (g - kg) ** 2 + (b - kb) ** 2) ** 0.5
                total += 1
                if dist <= _BACKGROUND_VALID_DISTANCE:
                    near += 1
        if total == 0 or (near / total) < _CORNER_VALID_RATIO:
            return False
    return True


def _chroma_key_to_alpha(img: Image.Image) -> Image.Image:
    """背景のクロマキー色に近い画素を透過にする。

    ピクセル単位のPythonループは大きな画像だと遅いため、`ImageChops`の
    バンド演算(C実装)だけで色距離とアルファのフェードを計算する。
    """
    rgb = img.convert("RGB")
    key_img = Image.new("RGB", rgb.size, _CHROMA_KEY_RGB)
    diff = ImageChops.difference(rgb, key_img)
    r, g, b = diff.split()
    # 各チャンネル差の合計(0〜765になりうるが255でクリップされる。
    # しきい値は255よりずっと小さいので、クリップは判定に影響しない)。
    dist = ImageChops.add(ImageChops.add(r, g), b)

    low, high = _CHROMA_KEY_TOLERANCE, _CHROMA_KEY_TOLERANCE * 2
    lut = [
        0 if v <= low else 255 if v >= high else int(255 * (v - low) / (high - low))
        for v in range(256)
    ]
    alpha = dist.point(lut)

    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def _build_prompt(prefix: str, emotion: str, mouth_state: str) -> str:
    name = _EN_NAMES.get(prefix, prefix)
    description = _EMOTION_DESCRIPTIONS.get(emotion, emotion)
    kr, kg, kb = _CHROMA_KEY_RGB
    mouth_instruction = (
        "wide open, mid-speech shape"
        if mouth_state == "open"
        else "fully closed"
    )
    return (
        f"The attached reference image shows this show's mascot character, "
        f"{name}, as a full-body illustration. Redraw the exact same "
        f"character in the exact same pose, camera framing, crop, and body "
        f"silhouette as the reference, but change the facial expression and "
        f"body language to convey this emotion: {description}. "
        f"IMPORTANT: the mouth must stay {mouth_instruction}, exactly like "
        f"the reference image — do not let the requested emotion change "
        f"whether the mouth is open or closed; only the eyes, eyebrows, "
        f"cheeks, and body language/pose should change. "
        f"Preserve the character's identity exactly — same hairstyle, hair "
        f"color, outfit, color palette, and line-art style as the "
        f"reference. Do not depict a different character. Do not add any "
        f"text, logos, or watermarks. "
        f"Background: fill the entire background edge to edge with a "
        f"single, perfectly flat, solid chroma-key color rgb({kr},{kg},{kb}) "
        f"— no gradient, no shadow, no texture, no other objects, nothing "
        f"but that flat color behind the character, so it can be keyed out "
        f"in post-production. The character itself must not contain this "
        f"magenta color anywhere on its body, hair, or outfit."
    )


def get_emotion_asset_path(prefix: str, emotion: str, mouth_state: str) -> Path | None:
    """感情差分の立ち絵PNG(透過済み)のパスを返す。

    `prefix`はvideo_assembler.CHARACTER_PREFIXESの値("tsumugi"/
    "zundamon")。`emotion`が"neutral"の場合は既存の通常立ち絵
    (`{prefix}_{state}.png`)をそのまま返す。それ以外の感情は、
    `assets/characters/`に`{prefix}_{emotion}_{state}.png`として
    キャッシュがあればそれを返し、無ければ通常立ち絵を参照画像に
    Geminiで生成してキャッシュする。GEMINI_API_KEY未設定・参照画像が
    無い・生成失敗のいずれでもNoneを返す(呼び出し側は通常表情に
    フォールバックする)。
    """
    if emotion == "neutral":
        path = CHARACTER_ASSETS_DIR / f"{prefix}_{mouth_state}.png"
        return path if path.exists() else None

    cached = CHARACTER_ASSETS_DIR / f"{prefix}_{emotion}_{mouth_state}.png"
    if cached.exists():
        return cached

    reference = CHARACTER_ASSETS_DIR / f"{prefix}_{mouth_state}.png"
    if not reference.exists():
        return None

    raw_path = CHARACTER_ASSETS_DIR / f"_tmp_{prefix}_{emotion}_{mouth_state}.png"
    # 最終画像もまず一時ファイルに書き込んでから、キャッシュ本パスへ
    # アトミックにrenameする(Path.replace)。直接cachedへ保存すると、
    # 書き込み中にプロセスが強制終了された場合(Ctrl-C・OOM killer・
    # レンダリングタイムアウト等)、壊れた(途中までしか書かれていない)PNGが
    # キャッシュ本パスに残ってしまう。get_emotion_asset_pathはキャッシュの
    # 存在チェックしか行わない(内容の検証はしない)ため、一度壊れると
    # 手動で削除するまで永久に壊れたファイルを返し続けてしまっていた。
    final_tmp_path = CHARACTER_ASSETS_DIR / f"_tmp_final_{prefix}_{emotion}_{mouth_state}.png"
    try:
        for attempt in range(_MAX_GENERATION_ATTEMPTS):
            generated = generate_slide_image(
                _build_prompt(prefix, emotion, mouth_state),
                raw_path,
                aspect_ratio="4:3",
                reference_images=[reference],
            )
            if generated is None:
                continue
            img = Image.open(generated)
            if not _has_chroma_key_background(img):
                # プロンプトの背景指示を無視して別の背景(白など)で生成
                # したり、無関係な図形を描き足したりしてしまうことが実際に
                # あった。キー色が存在しないと透過にできず背景がそのまま
                # 映り込んでしまうため、この結果は保存せず破棄して
                # (コストはかかるが)再試行する。
                continue
            transparent = _chroma_key_to_alpha(img)
            transparent.save(final_tmp_path)
            final_tmp_path.replace(cached)
            return cached
        return None
    except Exception:  # noqa: BLE001 表情差し替えは演出上のおまけなので、失敗してもパイプライン全体を止めない
        return None
    finally:
        raw_path.unlink(missing_ok=True)
        final_tmp_path.unlink(missing_ok=True)
