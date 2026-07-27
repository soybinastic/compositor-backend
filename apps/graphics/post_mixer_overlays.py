"""Post-mixer gdkpixbufoverlay helpers for static / lightly animated graphics.

Still layers must not use force-live compositor sink pads: a full-canvas RGBA
appsrc pad competes with camera aggregation and starves x264/RTMP. Drawing via
gdkpixbufoverlay after the mixer blends once per output frame with no extra
aggregator pad and no continuous frame pusher.

Only two overlay elements are wired into the pipeline:
  - graphics_stack: one software-composited RGBA for banner/overlay/logo/chat/qr
  - ticker: separately animated via offset-x
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import gi
from PIL import Image

gi.require_version('Gst', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib, Gst  # noqa: E402

from apps.graphics.constants import (
    LAYER_BACKGROUND,
    LAYER_CHAT,
    LAYER_LOGO,
    LAYER_OVERLAY,
    LAYER_QR,
    LAYER_TICKER,
)

logger = logging.getLogger(__name__)

LAYER_GRAPHICS_STACK = 'graphics_stack'

# Pipeline elements (bottom → top). Stack is one blend; ticker scrolls separately.
POST_MIXER_OVERLAY_KEYS: tuple[str, ...] = (
    LAYER_GRAPHICS_STACK,
    LAYER_TICKER,
)

# Bottom → top within the software-composited stack.
# Background is first: margin frame only (not a full opaque canvas), so live
# video shows through without an extra force-live compositor pad.
STATIC_STACK_ORDER: tuple[str, ...] = (
    LAYER_BACKGROUND,
    'banner_primary',
    'banner_secondary',
    LAYER_OVERLAY,
    LAYER_LOGO,
    LAYER_CHAT,
    LAYER_QR,
)

# Camera tile inset (px) when a background is active — creates the visible frame.
BACKGROUND_TILE_INSET = 48


@dataclass
class PixbufLayerState:
    layer_key: str
    signature: str = ''
    geometry: tuple[int, int, int, int] = (0, 0, 1, 1)
    visible: bool = False
    ticker_width: int = 0
    ticker_direction: str = 'rtl'
    ticker_speed: float = 2.0
    # Source image for stack rebuild / ticker; keep pixels alive for GdkPixbuf.
    _image: Image.Image | None = field(default=None, repr=False)
    _pixel_bytes: bytes | None = field(default=None, repr=False)


def create_post_mixer_overlay_elements() -> dict[str, Gst.Element]:
    elements: dict[str, Gst.Element] = {}
    for key in POST_MIXER_OVERLAY_KEYS:
        element = Gst.ElementFactory.make('gdkpixbufoverlay', f'gfx_overlay_{key}')
        if element is None:
            raise RuntimeError('gdkpixbufoverlay is required for graphics overlays')
        element.set_property('alpha', 0.0)
        elements[key] = element
    return elements


def image_to_pixbuf(image: Image.Image) -> tuple[GdkPixbuf.Pixbuf, bytes]:
    rgba = image.convert('RGBA')
    raw = rgba.tobytes()
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(raw),
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        rgba.width,
        rgba.height,
        rgba.width * 4,
    )
    return pixbuf, raw


def _paste_bg_strip(
    canvas: Image.Image,
    bg: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return
    canvas.paste(bg.crop((left, top, right, bottom)), (left, top))


def _composite_background_margins(
    canvas: Image.Image,
    background: Image.Image,
    cutouts: list[tuple[int, int, int, int]],
) -> None:
    """
    Draw background only outside camera tiles (opaque frame / gutters).

    Avoids filling the full canvas then punching holes — less CPU on rebuild
    and far fewer opaque pixels for gdkpixbufoverlay to blend each frame.
    """
    bg = background.convert('RGBA')
    if bg.size != canvas.size:
        bg = bg.resize(canvas.size, Image.Resampling.LANCZOS)

    if not cutouts:
        canvas.alpha_composite(bg, dest=(0, 0))
        return

    # Single cutout (CONTAIN / FULLSCREEN host): four margin strips.
    if len(cutouts) == 1:
        x, y, w, h = cutouts[0]
        x = max(0, int(x))
        y = max(0, int(y))
        w = max(1, int(w))
        h = max(1, int(h))
        x2 = min(canvas.width, x + w)
        y2 = min(canvas.height, y + h)
        x = min(x, canvas.width)
        y = min(y, canvas.height)
        _paste_bg_strip(canvas, bg, (0, 0, canvas.width, y))
        _paste_bg_strip(canvas, bg, (0, y2, canvas.width, canvas.height))
        _paste_bg_strip(canvas, bg, (0, y, x, y2))
        _paste_bg_strip(canvas, bg, (x2, y, canvas.width, y2))
        return

    # Multiple tiles: paste full then clear camera rects.
    canvas.alpha_composite(bg, dest=(0, 0))
    for x, y, w, h in cutouts:
        x = max(0, int(x))
        y = max(0, int(y))
        w = max(1, int(w))
        h = max(1, int(h))
        if x >= canvas.width or y >= canvas.height:
            continue
        w = min(w, canvas.width - x)
        h = min(h, canvas.height - y)
        if w <= 0 or h <= 0:
            continue
        canvas.paste(Image.new('RGBA', (w, h), (0, 0, 0, 0)), (x, y))


def compose_static_stack(
    layers: dict[str, PixbufLayerState],
    *,
    canvas_w: int,
    canvas_h: int,
    video_cutouts: list[tuple[int, int, int, int]] | None = None,
) -> Image.Image | None:
    canvas = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    any_visible = False
    cutouts = list(video_cutouts or [])
    for key in STATIC_STACK_ORDER:
        state = layers.get(key)
        if state is None or not state.visible or state._image is None:
            continue
        x, y, w, h = state.geometry
        w = max(1, int(w))
        h = max(1, int(h))
        img = state._image.convert('RGBA')
        if key == LAYER_BACKGROUND:
            _composite_background_margins(canvas, img, cutouts)
        else:
            if img.size != (w, h):
                img = img.resize((w, h), Image.Resampling.LANCZOS)
            canvas.alpha_composite(img, dest=(max(0, int(x)), max(0, int(y))))
        any_visible = True
    return canvas if any_visible else None


def apply_pixbuf_to_overlay(
    element: Gst.Element,
    image: Image.Image,
    geometry: tuple[int, int, int, int],
    *,
    state: PixbufLayerState,
) -> None:
    x, y, w, h = geometry
    if image.size != (max(1, w), max(1, h)):
        image = image.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
    pixbuf, raw = image_to_pixbuf(image)
    state._image = image
    state._pixel_bytes = raw
    element.set_property('pixbuf', pixbuf)
    element.set_property('offset-x', int(x))
    element.set_property('offset-y', int(y))
    if element.find_property('overlay-width') is not None:
        element.set_property('overlay-width', max(1, int(w)))
    if element.find_property('overlay-height') is not None:
        element.set_property('overlay-height', max(1, int(h)))
    element.set_property('alpha', 1.0)
    state.geometry = geometry
    state.visible = True


def clear_pixbuf_overlay(element: Gst.Element, state: PixbufLayerState) -> None:
    element.set_property('alpha', 0.0)
    try:
        element.set_property('pixbuf', None)
    except Exception:
        logger.debug('Failed to clear pixbuf on %s', state.layer_key)
    state.visible = False
    state._image = None
    state._pixel_bytes = None
    state.signature = ''
