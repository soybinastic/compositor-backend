"""Visibility rules for graphics layers."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from django.conf import settings

from apps.graphics.constants import BACKGROUND_VISIBLE_LAYOUTS


def resolve_url(config: dict[str, Any] | None) -> str | None:
    if not config:
        return None
    for key in ('source', 'url'):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_http_image_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def background_should_show(config: dict[str, Any] | None, layout: str) -> bool:
    if getattr(settings, 'COMPOSITOR_DISABLE_BACKGROUND', False):
        return False
    if layout not in BACKGROUND_VISIBLE_LAYOUTS:
        return False
    return bool(resolve_url(config))


def overlay_should_show(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    if not config.get('is_active', False):
        return False
    return bool(resolve_url(config))


def logo_should_show(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    if not config.get('is_active', False):
        return False
    return bool(resolve_url(config))


def qr_should_show(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    if not config.get('is_shown', False):
        return False
    return is_http_image_url(resolve_url(config))


def _truthy_display_flag(config: dict[str, Any]) -> bool | None:
    """
    Return True/False when an explicit display flag is present, else None.

    None means "preserve existing banners" on update (layout-only / missing flag).
    """
    for key in ('is_display_names', 'is_display'):
        if key in config:
            return bool(config[key])
    parent = config.get('parent_data')
    if isinstance(parent, dict):
        for key in ('is_display_names', 'is_display'):
            if key in parent:
                return bool(parent[key])
    return None


def banner_text_parts(config: dict[str, Any] | None) -> tuple[str, str]:
    if not config:
        return '', ''
    parent = config.get('parent_data') if isinstance(config.get('parent_data'), dict) else {}
    text_overlay = (
        config.get('textOverlay') if isinstance(config.get('textOverlay'), dict) else {}
    )
    graphic_banner = config.get('graphic') if isinstance(config.get('graphic'), dict) else {}
    banner = (
        graphic_banner.get('banner')
        if isinstance(graphic_banner.get('banner'), dict)
        else config
    )

    title = (
        parent.get('title')
        or text_overlay.get('title')
        or banner.get('title')
        or config.get('title')
        or ''
    )
    description = (
        parent.get('description')
        or text_overlay.get('description')
        or banner.get('description')
        or config.get('description')
        or ''
    )
    return str(title).strip(), str(description).strip()


def banner_should_show(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    flag = _truthy_display_flag(config)
    if flag is False:
        return False
    if flag is None:
        # No explicit flag: only show when there is already something to display
        # and caller treats missing flag as preserve — service layer handles preserve.
        title, description = banner_text_parts(config)
        return bool(title or description)
    title, description = banner_text_parts(config)
    return bool(title or description)


def banner_preserve_on_missing_flag(
    incoming: dict[str, Any] | None,
    existing: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """If display flag is missing on update, keep existing banner config."""
    if incoming is None:
        return None
    if _truthy_display_flag(incoming) is None and existing is not None:
        return existing
    return incoming


def ticker_text(config: dict[str, Any] | None) -> str:
    if not config:
        return ''
    text_overlay = (
        config.get('textOverlay') if isinstance(config.get('textOverlay'), dict) else {}
    )
    return str(
        config.get('tickerText')
        or config.get('ticker_description')
        or text_overlay.get('text')
        or config.get('text')
        or ''
    ).strip()


def ticker_should_show(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    if config.get('tickerEnabled', True) is False:
        return False
    return bool(ticker_text(config))


def chat_should_show(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    return bool(config.get('enabled', False))
