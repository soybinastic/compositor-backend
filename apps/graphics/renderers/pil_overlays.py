"""PIL renderers for banner, ticker, and chat overlay bitmaps."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFont

from apps.graphics.constants import (
    BANNER_HORIZONTAL_PADDING,
    BANNER_MIN_WIDTH,
    BANNER_X,
)
from apps.graphics.typography import FontFaceLoader, create_default_font_loader


def _resolve_font(
    size: int,
    *,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> ImageFont.ImageFont:
    loader = font_loader or create_default_font_loader()
    return loader.load(font_family, size)


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


def _normalize_banner_theme(theme: str) -> str:
    key = str(theme or 'classic').lower()
    if key in ('plain', 'classic'):
        return 'classic'
    if key == 'accent':
        return 'default'
    return key


def banner_bar_height(font_size: int) -> int:
    return max(48, font_size + 24)


def banner_text_width(
    text: str,
    font_size: int,
    *,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> int:
    if not text.strip():
        return 0
    font = _resolve_font(font_size, font_family=font_family, font_loader=font_loader)
    probe = Image.new('RGBA', (1, 1))
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0]) + BANNER_HORIZONTAL_PADDING


def banner_content_width(
    title: str,
    description: str,
    font_size: int,
    *,
    canvas_w: int,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> int:
    title_width = banner_text_width(
        title,
        font_size,
        font_family=font_family,
        font_loader=font_loader,
    )
    desc_font_size = max(16, font_size - 8)
    desc_width = banner_text_width(
        description,
        desc_font_size,
        font_family=font_family,
        font_loader=font_loader,
    )
    content_width = max(title_width, desc_width, BANNER_MIN_WIDTH)
    max_width = max(1, canvas_w - BANNER_X * 2)
    return min(content_width, max_width)


def _draw_banner_shape(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    height: int,
    theme: str,
    bg: tuple[int, int, int, int],
    accent: tuple[int, int, int, int],
) -> None:
    w = max(1, width - 1)
    h = max(1, height - 1)

    if theme == 'rounded':
        draw.rounded_rectangle([0, 0, w, h], radius=12, fill=bg)
        return

    if theme == 'pill':
        radius = max(8, height // 2)
        draw.rounded_rectangle([0, 0, w, h], radius=radius, fill=bg)
        return

    if theme == 'outlined':
        fill = (bg[0], bg[1], bg[2], min(bg[3], 165))
        draw.rectangle([0, 0, width, height], fill=fill)
        draw.rectangle([0, 0, w, h], outline=accent, width=2)
        return

    draw.rectangle([0, 0, width, height], fill=bg)

    if theme == 'default':
        draw.rectangle([0, 0, 8, height], fill=accent)
    elif theme == 'bracket':
        draw.rectangle([0, 0, 8, height], fill=accent)
        draw.rectangle([max(0, width - 8), 0, width, height], fill=accent)


def render_banner_bar(
    *,
    width: int,
    title: str,
    theme: str = 'classic',
    primary: str = '',
    secondary: str = '',
    accent: str = '',
    font_size: int = 36,
    is_primary: bool = True,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> Image.Image:
    height = banner_bar_height(font_size)
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    theme_key = _normalize_banner_theme(theme)
    bg = _parse_color(
        primary if is_primary else secondary,
        (20, 20, 20, 200) if is_primary else (55, 65, 81, 220),
    )
    accent_color = _parse_color(accent or secondary, (0, 160, 220, 255))
    fg = (255, 255, 255, 255)

    _draw_banner_shape(
        draw,
        width=width,
        height=height,
        theme=theme_key,
        bg=bg,
        accent=accent_color,
    )

    font = _resolve_font(font_size, font_family=font_family, font_loader=font_loader)
    draw.text((20, (height - font_size) // 2), title, font=font, fill=fg)
    return img


def render_ticker_bar(
    *,
    canvas_width: int,
    text: str,
    primary: str = '',
    secondary: str = '',
    font_size: int = 28,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> Image.Image:
    """Legacy single-strip renderer (background + text). Prefer split renderers."""
    height = ticker_bar_height(font_size)
    bg = render_ticker_background(canvas_width=canvas_width, primary=primary, font_size=font_size)
    text_strip = render_ticker_text_strip(
        canvas_width=canvas_width,
        text=text,
        secondary=secondary,
        font_size=font_size,
        font_family=font_family,
        font_loader=font_loader,
    )
    combined = bg.copy()
    combined.alpha_composite(text_strip, dest=(0, 0))
    return combined


def ticker_bar_height(font_size: int = 28) -> int:
    return max(40, font_size + 16)


def render_ticker_background(
    *,
    canvas_width: int,
    primary: str = '',
    font_size: int = 28,
) -> Image.Image:
    height = ticker_bar_height(font_size)
    bg = _parse_color(primary, (0, 0, 0, 160))
    return Image.new('RGBA', (max(1, canvas_width), height), bg)


def render_ticker_text_strip(
    *,
    canvas_width: int,
    text: str,
    secondary: str = '',
    font_size: int = 28,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> Image.Image:
    height = ticker_bar_height(font_size)
    font = _resolve_font(font_size, font_family=font_family, font_loader=font_loader)
    probe = Image.new('RGBA', (1, 1))
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    strip_w = max(canvas_width * 2, text_w + BANNER_HORIZONTAL_PADDING)
    img = Image.new('RGBA', (strip_w, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fg = _parse_color(secondary, (255, 255, 255, 255))
    draw.text((20, (height - font_size) // 2), text, font=font, fill=fg)
    return img


def format_countdown_label(seconds_remaining: int) -> str:
    minutes, seconds = divmod(max(0, seconds_remaining), 60)
    return f'{minutes}:{seconds:02d}'


def render_countdown_overlay(
    *,
    canvas_width: int,
    canvas_height: int,
    seconds_remaining: int,
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> Image.Image:
    label = format_countdown_label(seconds_remaining)
    box_w = min(420, max(220, canvas_width // 4))
    box_h = min(180, max(100, canvas_height // 5))
    img = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 180))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=16, fill=(0, 0, 0, 200))
    font_size = max(36, min(96, box_h // 2))
    font = _resolve_font(font_size, font_family=font_family, font_loader=font_loader)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.text(
        ((box_w - text_w) // 2, (box_h - text_h) // 2 - 4),
        label,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return img


def render_chat_panel(
    *,
    width: int,
    height: int,
    messages: list[dict[str, Any]],
    font_family: str | None = None,
    font_loader: FontFaceLoader | None = None,
) -> Image.Image:
    img = Image.new('RGBA', (width, height), (10, 10, 14, 180))
    draw = ImageDraw.Draw(img)
    font = _resolve_font(22, font_family=font_family, font_loader=font_loader)
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
