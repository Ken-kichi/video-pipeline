"""動画のサムネイル(16:9, 1280x720)を生成する。

構成:
- 背景: Gemini生成の挿絵(あれば、スライドの背景生成と同じ仕組みを再利用) or
  アクセントカラーのグラデーション
- キャッチコピー: 縁取り付きの大きな文字で表示(背景の絵柄を選ばず読める)
- キャラクター立ち絵(assets/characters/に口を開けた状態のPNGがあれば): 
  本編の動画と同じ配置(つむぎ=左下、ずんだもん=右下)で表示し、動画本体との
  見た目の一貫性を持たせる
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoSansJP-Bold.otf"
CHARACTER_ASSETS_DIR = Path(__file__).parent / "assets" / "characters"
CHARACTER_PREFIXES = {"つむぎ": "tsumugi", "ずんだもん": "zundamon"}
CHARACTER_POSITIONS = {"つむぎ": "left", "ずんだもん": "right"}
CHARACTER_DISPLAY_HEIGHT = 520
CHARACTER_MARGIN_X = 10

ACCENT_COLOR = (76, 110, 245)  # #4C6EF5
ACCENT_COLOR_DARK = (30, 40, 110)
TEXT_COLOR = "#FFFFFF"
TEXT_OUTLINE_COLOR = "#1A1A2E"
SUB_TEXT_COLOR = "#FFE066"


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


def build_thumbnail(
    main_text: str,
    sub_text: str,
    output_path: str | Path,
    background_path: str | Path | None = None,
) -> Path:
    """サムネイル画像(1280x720, 16:9)を組み立てて保存する。"""
    if background_path and Path(background_path).exists():
        bg = Image.open(background_path).convert("RGB")
        scale = max(THUMBNAIL_WIDTH / bg.width, THUMBNAIL_HEIGHT / bg.height)
        bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1), Image.LANCZOS)
        left = (bg.width - THUMBNAIL_WIDTH) // 2
        top = (bg.height - THUMBNAIL_HEIGHT) // 2
        img = bg.crop((left, top, left + THUMBNAIL_WIDTH, top + THUMBNAIL_HEIGHT))
        # 背景画像の上に文字を読みやすくするための半透明の暗いスクリムを重ねる
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle([(0, 0), img.size], fill=(20, 20, 40, 140))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    else:
        img = _make_gradient_background(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)

    draw = ImageDraw.Draw(img)

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

    def draw_outlined_centered(text: str, font: ImageFont.FreeTypeFont, y: int, fill: str) -> int:
        if not text:
            return y
        width = draw.textlength(text, font=font)
        x = (THUMBNAIL_WIDTH - width) / 2
        draw.text(
            (x, y), text, font=font, fill=fill, stroke_width=8, stroke_fill=TEXT_OUTLINE_COLOR
        )
        return y + int(font.size * 1.25)

    total_height = int(main_font.size * 1.25)
    if sub_text:
        total_height += int(sub_font.size * 1.25)
    y = (THUMBNAIL_HEIGHT - total_height) // 2 - 40

    y = draw_outlined_centered(main_text, main_font, y, TEXT_COLOR)
    draw_outlined_centered(sub_text, sub_font, y, SUB_TEXT_COLOR)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path
