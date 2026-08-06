"""Build live per-tile camera-off placeholder video for the compositor.

Mirrors studio-frontend PreviewParticipant camera-off styling:
zinc-800 fill, centered zinc-700 circle avatar, zinc-200 initials, zinc-400 name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import gi
from PIL import Image, ImageDraw, ImageFont

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from apps.graphics.post_mixer_overlays import image_to_pixbuf

logger = logging.getLogger(__name__)

# Tailwind zinc palette used by PreviewParticipant.tsx
_BG = (39, 39, 42, 255)  # zinc-800
_AVATAR = (63, 63, 70, 255)  # zinc-700
_INITIALS = (228, 228, 231, 255)  # zinc-200
_NAME = (161, 161, 170, 255)  # zinc-400


@dataclass(frozen=True)
class PlaceholderKeepAlive:
    """Keep GdkPixbuf pixel backing alive for the lifetime of the overlay."""

    raw: bytes
    pixbuf: object


def placeholder_initials(display_name: str) -> str:
    cleaned = (display_name or '').replace(' (You)', '').strip()
    if not cleaned:
        return '?'
    parts = [part for part in cleaned.split() if part]
    letters = ''.join(part[0] for part in parts)[:2]
    return letters.upper() or '?'


def _placeholder_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
            if bold
            else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        ),
        (
            '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
            if bold
            else '/System/Library/Fonts/Supplemental/Arial.ttf'
        ),
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'
        if bold
        else '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_participant_placeholder_image(
    *,
    display_name: str,
    width: int,
    height: int,
) -> Image.Image:
    """
    Draw a studio-like camera-off plate.

    Sized to the ingest frame; videoscale in the ingest tail fits it to the tile.
    """
    width = max(2, int(width))
    height = max(2, int(height))
    label = (display_name or '').replace(' (You)', '').strip() or 'Guest'
    initials = placeholder_initials(label)

    img = Image.new('RGBA', (width, height), _BG)
    draw = ImageDraw.Draw(img)

    short = min(width, height)
    # ~PreviewParticipant: 48px avatar on a ~200–280px-tall tile.
    diameter = max(48, int(short * 0.22))
    cx, cy = width // 2, int(height * 0.44)
    x0, y0 = cx - diameter // 2, cy - diameter // 2
    draw.ellipse((x0, y0, x0 + diameter, y0 + diameter), fill=_AVATAR)

    initials_size = max(18, int(diameter * 0.38))
    initials_font = _placeholder_font(initials_size, bold=True)
    init_box = draw.textbbox((0, 0), initials, font=initials_font)
    init_w = init_box[2] - init_box[0]
    init_h = init_box[3] - init_box[1]
    draw.text(
        (cx - init_w / 2, cy - init_h / 2 - init_box[1] * 0.15),
        initials,
        font=initials_font,
        fill=_INITIALS,
    )

    name_size = max(14, int(short * 0.045))
    name_font = _placeholder_font(name_size, bold=False)
    # Truncate long names so they stay readable when the tile is small.
    max_chars = max(8, width // max(name_size // 2, 1))
    shown = label if len(label) <= max_chars else f'{label[: max(1, max_chars - 1)]}…'
    name_box = draw.textbbox((0, 0), shown, font=name_font)
    name_w = name_box[2] - name_box[0]
    name_y = y0 + diameter + max(10, int(short * 0.03))
    draw.text(
        (cx - name_w / 2, name_y),
        shown,
        font=name_font,
        fill=_NAME,
    )

    return img


def build_participant_placeholder_chain(
    *,
    peer_id: str,
    display_name: str,
    width: int,
    height: int,
    fps: int,
    ingest_tail: list[Gst.Element],
) -> tuple[list[Gst.Element], Gst.Element, PlaceholderKeepAlive]:
    """
    Live zinc plate + avatar into the same ingest tail used by RTP video.

    Returns (all_elements, output_element, keep_alive) where output links to the
    compositor sink. Callers must retain keep_alive while the overlay is live.
    """
    fps = max(1, int(fps))
    width = max(2, int(width))
    height = max(2, int(height))
    safe_id = peer_id.replace('/', '_').replace(' ', '_')

    plate = render_participant_placeholder_image(
        display_name=display_name,
        width=width,
        height=height,
    )
    pixbuf, raw = image_to_pixbuf(plate)
    keep_alive = PlaceholderKeepAlive(raw=raw, pixbuf=pixbuf)

    src = Gst.ElementFactory.make('videotestsrc', f'ph_src_{safe_id}')
    caps = Gst.ElementFactory.make('capsfilter', f'ph_caps_{safe_id}')
    overlay = Gst.ElementFactory.make('gdkpixbufoverlay', f'ph_pixbuf_{safe_id}')
    convert = Gst.ElementFactory.make('videoconvert', f'ph_convert_{safe_id}')

    if not all([src, caps, overlay, convert, *ingest_tail]):
        raise RuntimeError(f'Failed to create placeholder chain for {peer_id}')

    src.set_property('is-live', True)
    src.set_property('pattern', 2)  # black underlay; plate is opaque
    if src.find_property('do-timestamp') is not None:
        src.set_property('do-timestamp', True)
    caps.set_property(
        'caps',
        Gst.Caps.from_string(
            f'video/x-raw,format=I420,width={width},height={height},framerate={fps}/1'
        ),
    )
    overlay.set_property('pixbuf', pixbuf)
    overlay.set_property('offset-x', 0)
    overlay.set_property('offset-y', 0)
    if overlay.find_property('alpha') is not None:
        overlay.set_property('alpha', 1.0)

    elements = [src, caps, overlay, convert, *ingest_tail]
    logger.info(
        'Built styled camera-off placeholder for peer %s (%sx%s name=%r)',
        peer_id,
        width,
        height,
        (display_name or '').replace(' (You)', '').strip() or peer_id,
    )
    return elements, ingest_tail[-1], keep_alive
