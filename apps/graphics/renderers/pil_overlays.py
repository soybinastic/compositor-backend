"""PIL renderers for banner, ticker, and chat overlay bitmaps."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', size)
    except OSError:
        try:
            return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)
        except OSError:
            return ImageFont.load_default()


def _parse_color(value: str | None, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not value:
        return default
    text = value.strip().lstrip('#')
    try:
        if len(text) == 6:
            r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
            return r, g, b, 255
        if len(text) == 8:
            r, g, b, a = (
                int(text[0:2], 16),
                int(text[2:4], 16),
                int(text[4:6], 16),
                int(text[6:8], 16),
            )
            return r, g, b, a
    except ValueError:
        pass
    return default


def render_banner_bar(
    *,
    width: int,
    title: str,
    theme: str = 'plain',
    primary: str = '',
    secondary: str = '',
    font_size: int = 36,
    is_primary: bool = True,
) -> Image.Image:
    text = title if is_primary else title
    height = max(48, font_size + 24)
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = _parse_color(primary, (20, 20, 20, 200))
    accent = _parse_color(secondary, (0, 160, 220, 255))
    fg = (255, 255, 255, 255)

    if theme == 'rounded':
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=12, fill=bg)
    else:
        draw.rectangle([0, 0, width, height], fill=bg)
        if theme == 'accent':
            draw.rectangle([0, 0, 8, height], fill=accent)

    font = _font(font_size)
    draw.text((20, (height - font_size) // 2), text, font=font, fill=fg)
    return img


def render_ticker_bar(
    *,
    canvas_width: int,
    text: str,
    primary: str = '',
    secondary: str = '',
    font_size: int = 28,
) -> Image.Image:
    height = max(40, font_size + 16)
    # Wide strip so scrolling has room; pad will clip via compositor sizing.
    strip_w = max(canvas_width * 2, font_size * max(len(text), 1))
    bg = _parse_color(primary, (0, 0, 0, 160))
    fg = _parse_color(secondary, (255, 255, 255, 255))
    img = Image.new('RGBA', (strip_w, height), bg)
    draw = ImageDraw.Draw(img)
    font = _font(font_size)
    draw.text((20, (height - font_size) // 2), text, font=font, fill=fg)
    return img


def render_chat_panel(
    *,
    width: int,
    height: int,
    messages: list[dict[str, Any]],
) -> Image.Image:
    img = Image.new('RGBA', (width, height), (10, 10, 14, 180))
    draw = ImageDraw.Draw(img)
    font = _font(22)
    y = 16
    for message in messages[-20:]:
        author = str(message.get('author') or '')
        text = str(message.get('text') or message.get('message') or '')
        line = f'{author}: {text}' if author else text
        if not line:
            continue
        draw.text((16, y), line[:120], font=font, fill=(240, 240, 240, 255))
        y += 36
        if y > height - 40:
            break
    return img


def image_to_rgba_bytes(image: Image.Image) -> tuple[bytes, int, int]:
    rgba = image.convert('RGBA')
    return rgba.tobytes(), rgba.width, rgba.height
