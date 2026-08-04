"""動画のサムネイル(16:9, 1280x720)を生成する。

2つの生成方法がある:
- generate_thumbnail_with_gemini(): Geminiに背景・イラスト・文字を丸ごと
  生成させる(推奨。文字と背景を一体で描くため、テキストとイラストの
  レイアウトが自然に噛み合う)。文字精度が重要なので、通常のスライド背景
  よりも高精度な GEMINI_THUMBNAIL_MODEL を使う
- build_thumbnail(): Pillowでテキスト・キャラクター立ち絵を個別に重ねて
  描く方式(GEMINI_API_KEY未設定時のフォールバック)。この方式は、背景の
  絵柄やレイアウトを考慮せず固定位置に要素を重ねるだけなので、キャラクターと
  文字が重なる不具合が実際に起きたことがある。文字を画面上部、キャラクターを
  下部に固定して重ならないようにしている
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Bold.otf"
CHARACTER_ASSETS_DIR = Path(__file__).parent / "assets" / "characters"
CHARACTER_PREFIXES = {"つむぎ": "tsumugi", "ずんだもん": "zundamon"}
CHARACTER_POSITIONS = {"つむぎ": "left", "ずんだもん": "right"}
# 上部に固定するテキストと重ならないよう、キャラクターの高さは控えめにする
CHARACTER_DISPLAY_HEIGHT = 420
CHARACTER_MARGIN_X = 10

ACCENT_COLOR = (76, 110, 245)  # #4C6EF5
ACCENT_COLOR_DARK = (30, 40, 110)
TEXT_COLOR = "#FFFFFF"
TEXT_OUTLINE_COLOR = "#1A1A2E"
SUB_TEXT_COLOR = "#FFE066"
# テキストブロックの上端の固定位置(px)。キャラクターは下部に配置するため、
# 上部に寄せることで重なりを避ける
TEXT_TOP_MARGIN = 50

# Geminiにサムネイルを丸ごと生成させる際のスタイル指定。
# 最初は「文字+汎用的な背景」にしかならず物足りないという指摘を受け、
# 比較図・アイコン・パネルを積極的にデザインさせる指示に変えたところ、
# 今度は情報量が多すぎて逆にクリックされにくいサムネイルになってしまった
# (実際に生成した画像で、小さすぎて読めない文字が並ぶ複数パネル構成に
# なることが確認された)。YouTubeのサムネイルは120〜350px程度の小さい
# サイズで一瞬(1〜2秒)見て判断されるものなので、情報量を絞って「1つの
# 焦点+大きな文字」に立ち戻す方向に調整している。
# さらに、実際に「良質なサムネイルギャラリー」を確認したところ、共通して
# いたのは(1)濃い1色の力強い背景(オレンジ・黄色・黒など。dark navyに限らない)
# (2)視覚要素は写真1枚・イラスト1体・単純な図形1つのいずれかに絞られている
# (3)補助的な要素があっても「Ai」アイコンのような小さいバッジ程度、という
# 3点だったため、これも指示に反映している。
GEMINI_THUMBNAIL_STYLE = (
    "YouTube thumbnail design, 16:9 aspect ratio. This must look like a real, "
    "high click-through-rate YouTube thumbnail — NOT a slide, infographic, or "
    "diagram. It will typically be viewed at a tiny size (roughly 120-350px "
    "wide) while scrolling, so it needs an extremely simple composition with "
    "exactly ONE clear focal point. "
    "The headline text is the most important element: render it huge, bold, "
    "and ultra-legible, filling a large portion of the frame, with strong "
    "outline/contrast so it reads instantly even at a tiny preview size. "
    "You may add at most ONE simple, bold supporting visual that reinforces "
    "the headline's meaning — this can be a single striking photo/portrait-"
    "style visual (e.g. a reacting face), a single illustrated character, or "
    "one simple icon/symbol/before-after visual. Do NOT create multiple "
    "side-by-side panels, comparison boxes, flowcharts, or diagrams with "
    "several small labels — that reads as a slide, not a thumbnail, and "
    "becomes illegible at small preview sizes. Do not add any supporting "
    "text beyond the given headline/subheading, other than optionally one "
    "tiny badge-style label in a corner (a short 1-4 character tag, like a "
    "small icon chip) if it fits the theme. "
    "Use a bold, saturated, high-contrast solid-color background (a single "
    "strong color such as vivid orange, yellow, deep black, or dark navy — "
    "vary it to fit the content rather than defaulting to dark navy every "
    "time) rather than a busy or photorealistic backdrop. Professional, "
    "punchy, uncluttered. "
    "No watermarks, no logos, no borders."
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_PATH), size)


def _make_gradient_background(width: int, height: int) -> Image.Image:
    """背景画像が無い場合のフォールバック: 斜めのアクセントカラーグラデーション。"""
    img = Image.new("RGB", (width, height), ACCENT_COLOR)
    top = Image.new("RGB", (width, height), ACCENT_COLOR)
    bottom = Image.new("RGB", (width, height), ACCENT_COLOR_DARK)
    mask = Image.new("L", (width, height))
    mask_data = []
    for y in range(height):
        for x in range(width):
            ratio = (x + y) / (width + height)
            mask_data.append(int(255 * ratio))
    mask.putdata(mask_data)
    img = Image.composite(bottom, top, mask)
    return img


def _character_asset_paths() -> dict[str, Path]:
    """assets/characters/にある「口を開けた」状態のPNGを、サムネイル用に使う。"""
    assets: dict[str, Path] = {}
    for speaker, prefix in CHARACTER_PREFIXES.items():
        open_path = CHARACTER_ASSETS_DIR / f"{prefix}_open.png"
        if open_path.exists():
            assets[speaker] = open_path
    return assets


def _fit_height(image_path: Path, target_height: int) -> Image.Image:
    img = Image.open(image_path).convert("RGBA")
    scale = target_height / img.height
    new_size = (max(1, int(img.width * scale)), target_height)
    return img.resize(new_size, Image.LANCZOS)


def _cover_resize_crop(
    img: Image.Image, target_width: int, target_height: int
) -> Image.Image:
    """アスペクト比を保ったまま指定サイズを覆うようにリサイズ+中央クロップする。"""
    scale = max(target_width / img.width, target_height / img.height)
    resized = img.resize(
        (int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS
    )
    left = (resized.width - target_width) // 2
    top = (resized.height - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def build_gemini_thumbnail_prompt(
    main_text: str, sub_text: str, visual_summary: str = ""
) -> str:
    """Geminiにサムネイルを丸ごと生成させるためのプロンプトを組み立てる。

    最初はmain_text/sub_textだけを渡していたが「文字+汎用的な背景」にしか
    ならず物足りなかった。そこでvisual_summary(動画の核心的な内容の要約)を
    渡し、比較図・アイコンなどの図解を自律的にデザインさせる指示に変えた
    ところ、今度は情報量が多すぎて逆にクリックされにくいサムネイルになって
    しまった(実際に生成された画像で確認された)。visual_summaryは「複数の
    パネルを作る材料」ではなく「たった1つの視覚要素を選ぶための参考情報」
    として使うよう明示している。
    """
    lines = [
        "Design a YouTube video thumbnail image.",
        f'Prominently render this exact Japanese text as the large bold headline: "{main_text}"',
    ]
    if sub_text:
        lines.append(
            f'Render this exact Japanese text smaller, as a subheading near it: "{sub_text}"'
        )
    if visual_summary:
        lines.append(
            f"Content summary (for context only, to help you choose ONE simple "
            f"supporting visual — do not try to depict all of this in the image): "
            f"{visual_summary}"
        )
    lines.append(
        "Do not misspell, translate, or alter the given headline/subheading text. "
        "Do not add any other text, watermarks, or logos beyond what is described below."
    )
    lines.append(GEMINI_THUMBNAIL_STYLE)
    return " ".join(lines)


def generate_thumbnail_with_gemini(
    main_text: str, sub_text: str, output_path: str | Path, visual_summary: str = ""
) -> Path | None:
    """Geminiに背景・イラスト・文字を丸ごと生成させる。失敗した場合はNoneを返す。

    通常のスライド背景生成(image_generator.generate_slide_background)とは
    別に、文字精度重視のGEMINI_THUMBNAIL_MODELを明示的に指定する。
    """
    from video_pipeline.config import GEMINI_THUMBNAIL_MODEL
    from video_pipeline.image_generator import generate_slide_image

    output_path = Path(output_path)
    prompt = build_gemini_thumbnail_prompt(main_text, sub_text, visual_summary)
    raw_path = generate_slide_image(
        prompt,
        output_path.parent / "_thumbnail_gemini_raw.png",
        aspect_ratio="16:9",
        model=GEMINI_THUMBNAIL_MODEL,
    )
    if raw_path is None:
        return None

    # Geminiには16:9を指定しているが、実際の返却ピクセルサイズが1280x720と
    # 限らないため、念のため正規化する
    img = Image.open(raw_path).convert("RGB")
    cropped = _cover_resize_crop(img, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(output_path)
    return output_path


def build_thumbnail(
    main_text: str,
    sub_text: str,
    output_path: str | Path,
    background_path: str | Path | None = None,
) -> Path:
    """サムネイル画像(1280x720, 16:9)をPillowで組み立てて保存する(フォールバック用)。

    GEMINI_API_KEYが無い場合や、generate_thumbnail_with_gemini()が失敗した
    場合に使う。テキストを画面上部、キャラクターを下部に固定配置することで、
    背景の絵柄によらず両者が重ならないようにしている。
    """
    if background_path and Path(background_path).exists():
        bg = Image.open(background_path).convert("RGB")
        img = _cover_resize_crop(bg, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
        # 背景画像の上に文字を読みやすくするための半透明の暗いスクリムを重ねる
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle([(0, 0), img.size], fill=(20, 20, 40, 140))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    else:
        img = _make_gradient_background(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)

    # キャラクター立ち絵(あれば)を先に配置し、その後にテキストを重ねて可読性を確保する
    character_assets = _character_asset_paths()
    for speaker, path in character_assets.items():
        char_img = _fit_height(path, CHARACTER_DISPLAY_HEIGHT)
        position = CHARACTER_POSITIONS.get(speaker, "left")
        x = (
            CHARACTER_MARGIN_X
            if position == "left"
            else THUMBNAIL_WIDTH - char_img.width - CHARACTER_MARGIN_X
        )
        y = THUMBNAIL_HEIGHT - char_img.height
        img.paste(char_img, (x, y), char_img)

    draw = ImageDraw.Draw(img)

    main_font = _load_font(100)
    sub_font = _load_font(56)

    def draw_outlined_centered(
        text: str, font: ImageFont.FreeTypeFont, y: int, fill: str
    ) -> int:
        if not text:
            return y
        width = draw.textlength(text, font=font)
        x = (THUMBNAIL_WIDTH - width) / 2
        draw.text(
            (x, y),
            text,
            font=font,
            fill=fill,
            stroke_width=8,
            stroke_fill=TEXT_OUTLINE_COLOR,
        )
        return y + int(font.size * 1.25)

    # 画面上部に固定(キャラクターは下部固定なので重ならない)
    y = TEXT_TOP_MARGIN
    y = draw_outlined_centered(main_text, main_font, y, TEXT_COLOR)
    draw_outlined_centered(sub_text, sub_font, y, SUB_TEXT_COLOR)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path
