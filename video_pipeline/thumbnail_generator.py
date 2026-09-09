"""動画のサムネイル(16:9, 1280x720)を生成する。

2つの生成方法がある:
- generate_thumbnail_with_gemini() (推奨): Geminiに背景・キャラクター・
  文字を1回で丸ごと生成させる。キャラクター立ち絵を参照画像として渡す
  image-to-imageのため、内容に応じたポーズ・表情の作り込みが最も自然に
  仕上がる。ただし「参照画像に忠実に」とだけ指示すると、Geminiがポーズ・
  表情まで含めて立ち絵をそのまま複製してしまい、毎回ほぼ同じ「口を開けた
  だけの直立ポーズ」になる不具合が実際の生成結果で確認されたため、
  _character_reference_instruction()では「キャラクターとしての同一性
  (髪型・服装・配色・線画タッチ)は保つが、ポーズ・表情は内容に合わせて
  毎回描き直してよい」と明示している。
  一時期、内容を象徴するアイコン/ピクトグラムを添える指示も試したが、
  無理に図形を足すと不自然になる・キャラクターの表現だけで十分伝わる、
  という判断で撤回した。GEMINI_THUMBNAIL_STYLEにアイコン関連の指示を
  追加しないこと
- build_thumbnail(): Pillowでテキスト・キャラクター立ち絵を個別に重ねて
  描く方式(GEMINI_API_KEY未設定時、またはgenerate_thumbnail_with_gemini()
  失敗時のフォールバック)。固定位置に重ねるだけなので、キャラクターの
  ポーズ・表情は内容に関わらず常に同じになる
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Bold.otf"
CHARACTER_ASSETS_DIR = Path(__file__).parent / "assets" / "characters"
CHARACTER_PREFIXES = {"つむぎ": "tsumugi", "ずんだもん": "zundamon"}
CHARACTER_POSITIONS = {"つむぎ": "left", "ずんだもん": "right"}
CHARACTER_EN_NAMES = {"つむぎ": "Tsumugi", "ずんだもん": "Zundamon"}
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
#
# その後、「キャラクター立ち絵はいつも同じ構図の代わり映えしない見た目に
# なりがちで、内容の核心(過信・見逃しのような課題感)が伝わらない」という
# フィードバックを受け、内容を象徴するアイコン/ピクトグラムを追加する指示を
# 一時的に試した。しかし「無理にアイコンを入れないでほしい」という指摘を
# 受けて撤回している。キャラクターのポーズ・表情(下記の
# _character_reference_instruction()参照)だけで内容の核心を伝える方針とし、
# ここにアイコン関連の指示を戻さないこと。
GEMINI_THUMBNAIL_STYLE = (
    "YouTube thumbnail design, 16:9 aspect ratio. This must look like a real, "
    "high click-through-rate YouTube thumbnail — NOT a slide, infographic, or "
    "diagram. It will typically be viewed at a tiny size (roughly 120-350px "
    "wide) while scrolling, so it needs an extremely simple composition. "
    "The headline text is the most important element: render it huge, bold, "
    "and ultra-legible, filling a large portion of the frame, with strong "
    "outline/contrast so it reads instantly even at a tiny preview size. "
    "Include the show's mascot character(s) when reference image(s) are "
    "provided (see instructions below) — do not depict any other human face "
    "or invented character. The mascot's pose and expression (see "
    "instructions below) is the main — and only — supporting visual: it is "
    "how the thumbnail should communicate the content's emotional hook, so "
    "prioritize getting that right. Do not add any separate icon, symbol, "
    "pictogram, or graphic diagram alongside the mascot(s) — a forced extra "
    "graphic tends to look unnatural and clutters the frame; the "
    "character's pose and expression alone should carry the content's "
    "meaning. If there is no mascot reference image, keep the background "
    "itself simple with no added icon either. "
    "Do NOT create multiple side-by-side panels, comparison boxes, "
    "flowcharts, or diagrams with several small labels — that reads as a "
    "slide, not a thumbnail, and becomes illegible at small preview sizes. "
    "Do not add any supporting text beyond the given headline/subheading, "
    "other than optionally one tiny badge-style label in a corner (a short "
    "1-4 character tag, like a small icon chip) if it fits the theme. "
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


def _character_reference_instruction(speakers: list[str], visual_summary: str = "") -> str:
    """参照画像として渡すキャラクター立ち絵をどう扱うべきかの指示文を作る。

    以前は「参照画像に忠実に」とだけ指示していたところ、Geminiが立ち絵の
    ポーズ・表情までそのまま複製してしまい、毎回ほぼ同じ「口を開けただけの
    直立ポーズ」になる問題が実際の生成結果で確認された。そこで、保つべき
    なのはキャラクターとしての同一性(髪型・髪色・服装・配色・線画タッチ)
    だけであり、ポーズ・表情・向きは動画の内容に合わせて毎回自由に描き
    直してよい(むしろ描き直すべき)ことを明示する指示に変更した。
    """
    names = " and ".join(CHARACTER_EN_NAMES.get(s, s) for s in speakers)
    instruction = (
        f"The attached reference image(s) show this show's mascot character(s), "
        f"{names}. Preserve their identity — same hair color/style, same "
        f"outfit, same color palette, same line-art style — but do NOT simply "
        f"copy the reference image's neutral standing pose and expression. "
        f"Redraw them in a new, dramatic pose, facial expression, and body "
        f"orientation that specifically and vividly reacts to this video's "
        f"content"
    )
    if visual_summary:
        instruction += f" ({visual_summary})"
    instruction += (
        ", for example: wide-eyed shock with both hands on their cheeks, "
        "urgently pointing at something, recoiling with cold sweat, or a "
        "triumphant fist pump — whichever emotion actually fits the content. "
        "Do not depict a different character or a human face. Position them "
        "so they do not overlap or cover the headline text."
    )
    return instruction


def build_gemini_thumbnail_prompt(
    main_text: str,
    sub_text: str,
    visual_summary: str = "",
    character_speakers: list[str] | None = None,
) -> str:
    """Geminiにサムネイルを丸ごと生成させるためのプロンプトを組み立てる。

    最初はmain_text/sub_textだけを渡していたが「文字+汎用的な背景」にしか
    ならず物足りなかった。そこでvisual_summary(動画の核心的な内容の要約)を
    渡し、比較図・アイコンなどの図解を自律的にデザインさせる指示に変えた
    ところ、今度は情報量が多すぎて逆にクリックされにくいサムネイルになって
    しまった(実際に生成された画像で確認された)。visual_summaryは「複数の
    パネルを作る材料」ではなく「キャラクターに添える、たった1つの小さな
    象徴アイコンを選ぶための参考情報」として使うよう明示している。

    character_speakers: 参照画像として一緒に渡すキャラクター(つむぎ/ずんだもん)
    の話者名リスト。指定すると、汎用的な人物の顔などではなく、この立ち絵を
    忠実に使うようGeminiに指示する。
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
            f"Content summary (for context only — use it to decide the "
            f"mascot character(s)' pose/expression below, and optionally one "
            f"small supporting icon; do not try to depict all of this in the "
            f"image, and do not use it to add extra text): {visual_summary}"
        )
    lines.append(
        "Do not misspell, translate, or alter the given headline/subheading text. "
        "Do not add any other text, watermarks, or logos beyond what is described below."
    )
    lines.append(GEMINI_THUMBNAIL_STYLE)
    if character_speakers:
        lines.append(_character_reference_instruction(character_speakers, visual_summary))
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
    character_assets = _character_asset_paths()
    prompt = build_gemini_thumbnail_prompt(
        main_text,
        sub_text,
        visual_summary,
        character_speakers=list(character_assets.keys()),
    )
    raw_path = generate_slide_image(
        prompt,
        output_path.parent / "_thumbnail_gemini_raw.png",
        aspect_ratio="16:9",
        model=GEMINI_THUMBNAIL_MODEL,
        reference_images=list(character_assets.values()) or None,
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
