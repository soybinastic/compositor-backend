"""Geometry helpers for logo / QR / banner / chat placement."""

from __future__ import annotations

from typing import Any

from apps.graphics.constants import (
    BANNER_X,
    CHAT_EDGE_MARGIN,
    CHAT_PANEL_HEIGHT,
    CHAT_PANEL_WIDTH,
    LOGO_EDGE_INSET,
    LOGO_MAX_HEIGHT,
    LOGO_MAX_WIDTH,
    QR_CENTER_HEIGHT,
    QR_CENTER_WIDTH,
    QR_CORNER_SIZE,
    QR_EDGE_MARGIN,
    TICKER_CHAT_Y_NUDGE,
)


def scale_to_box(
    src_w: int,
    src_h: int,
    max_w: int,
    max_h: int,
) -> tuple[int, int]:
    if src_w <= 0 or src_h <= 0:
        return max_w, max_h
    scale = min(max_w / src_w, max_h / src_h)
    return max(1, int(src_w * scale)), max(1, int(src_h * scale))


def logo_geometry(
    canvas_w: int,
    canvas_h: int,
    src_w: int,
    src_h: int,
    config: dict[str, Any],
) -> tuple[int, int, int, int]:
    w, h = scale_to_box(src_w, src_h, LOGO_MAX_WIDTH, LOGO_MAX_HEIGHT)
    placement = (
        config.get('placement')
        or config.get('logoPosition')
        or config.get('position')
        or 'top-right'
    )
    if isinstance(placement, dict):
        return (
            int(placement.get('x', LOGO_EDGE_INSET)),
            int(placement.get('y', LOGO_EDGE_INSET)),
            w,
            h,
        )

    key = str(placement).lower().replace('_', '-')
    if key in ('top-left', 'topleft'):
        return LOGO_EDGE_INSET, LOGO_EDGE_INSET, w, h
    if key in ('bottom-left', 'bottomleft'):
        return LOGO_EDGE_INSET, canvas_h - h - LOGO_EDGE_INSET, w, h
    if key in ('bottom-right', 'bottomright'):
        return canvas_w - w - LOGO_EDGE_INSET, canvas_h - h - LOGO_EDGE_INSET, w, h
    # default top-right
    return canvas_w - w - LOGO_EDGE_INSET, LOGO_EDGE_INSET, w, h


def qr_geometry(
    canvas_w: int,
    canvas_h: int,
    config: dict[str, Any],
) -> tuple[int, int, int, int]:
    ow = int(config.get('overlay_width') or 0)
    oh = int(config.get('overlay_height') or 0)
    position = config.get('position')

    if isinstance(position, dict) and 'x' in position and 'y' in position:
        w = ow or int(position.get('w') or QR_CORNER_SIZE)
        h = oh or int(position.get('h') or QR_CORNER_SIZE)
        x = int(position['x'])
        y = int(position['y'])
        return _clamp_rect(x, y, w, h, canvas_w, canvas_h)

    key = str(position or 'bottomRight').lower().replace('_', '').replace('-', '')
    if key == 'center':
        w = ow or QR_CENTER_WIDTH
        h = oh or QR_CENTER_HEIGHT
        x = (canvas_w - w) // 2
        y = (canvas_h - h) // 2
        return _clamp_rect(x, y, w, h, canvas_w, canvas_h)

    w = ow or QR_CORNER_SIZE
    h = oh or QR_CORNER_SIZE
    if key in ('topleft',):
        return _clamp_rect(QR_EDGE_MARGIN, QR_EDGE_MARGIN, w, h, canvas_w, canvas_h)
    if key in ('topright',):
        return _clamp_rect(
            canvas_w - w - QR_EDGE_MARGIN,
            QR_EDGE_MARGIN,
            w,
            h,
            canvas_w,
            canvas_h,
        )
    if key in ('bottomleft',):
        return _clamp_rect(
            QR_EDGE_MARGIN,
            canvas_h - h - QR_EDGE_MARGIN,
            w,
            h,
            canvas_w,
            canvas_h,
        )
    # bottomRight default
    return _clamp_rect(
        canvas_w - w - QR_EDGE_MARGIN,
        canvas_h - h - QR_EDGE_MARGIN,
        w,
        h,
        canvas_w,
        canvas_h,
    )


def overlay_geometry(
    canvas_w: int,
    canvas_h: int,
    config: dict[str, Any],
) -> tuple[int, int, int, int]:
    position = config.get('position')
    if isinstance(position, dict):
        x = int(position.get('x', 0))
        y = int(position.get('y', 0))
        w = int(position.get('w', canvas_w))
        h = int(position.get('h', canvas_h))
        return _clamp_rect(x, y, w, h, canvas_w, canvas_h)
    return 0, 0, canvas_w, canvas_h


def banner_geometry(
    canvas_w: int,
    canvas_h: int,
    *,
    primary: bool,
    font_size: int,
    bar_height: int,
) -> tuple[int, int, int, int]:
    extra = 40 if font_size >= 70 else 0
    if primary:
        y = canvas_h - bar_height * 2 - 96 - extra
    else:
        y = canvas_h - bar_height - 40 - extra
    w = max(1, canvas_w - BANNER_X * 2)
    return BANNER_X, max(0, y), w, bar_height


def ticker_geometry(
    canvas_w: int,
    canvas_h: int,
    *,
    position: str,
    bar_height: int,
    chat_active: bool,
) -> tuple[int, int, int, int]:
    nudge = TICKER_CHAT_Y_NUDGE if chat_active else 0
    if str(position).lower() == 'top':
        y = 0
    else:
        y = canvas_h - bar_height - nudge
    return 0, max(0, y), canvas_w, bar_height


def chat_geometry(canvas_w: int, canvas_h: int) -> tuple[int, int, int, int]:
    w = min(CHAT_PANEL_WIDTH, canvas_w - CHAT_EDGE_MARGIN * 2)
    h = min(CHAT_PANEL_HEIGHT, canvas_h - CHAT_EDGE_MARGIN * 2)
    x = canvas_w - w - CHAT_EDGE_MARGIN
    y = CHAT_EDGE_MARGIN
    return x, y, w, h


def _clamp_rect(
    x: int,
    y: int,
    w: int,
    h: int,
    canvas_w: int,
    canvas_h: int,
) -> tuple[int, int, int, int]:
    w = max(1, min(w, canvas_w))
    h = max(1, min(h, canvas_h))
    x = max(0, min(x, canvas_w - w))
    y = max(0, min(y, canvas_h - h))
    return x, y, w, h
