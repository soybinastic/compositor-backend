"""Background music scene config normalization and validation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from apps.scenes.constants import DEFAULT_BACKGROUND_MUSIC_CONFIG

SUPPORTED_BACKGROUND_MUSIC_EXTENSIONS = (
    '.mp3',
    '.wav',
    '.aac',
    '.m4a',
    '.ogg',
)

BackgroundMusicConfig = dict[str, Any]


def normalize_background_music_config(
    config: dict[str, Any] | None,
) -> BackgroundMusicConfig:
    """Merge partial scene config with defaults."""
    base = dict(DEFAULT_BACKGROUND_MUSIC_CONFIG)
    if not config:
        return base

    normalized = {
        'version': 1,
        'enabled': bool(config.get('enabled', base['enabled'])),
        'track': normalize_background_music_track(config.get('track')),
        'volume': _clamp_volume(config.get('volume', base['volume'])),
        'loop': bool(config.get('loop', base['loop'])),
        'muted': bool(config.get('muted', base['muted'])),
    }
    return normalized


def normalize_background_music_track(track: Any) -> dict[str, str] | None:
    if not track or not isinstance(track, dict):
        return None

    url = str(track.get('url') or track.get('source') or '').strip()
    if not url:
        return None

    title = str(track.get('title') or '').strip() or _title_from_url(url)
    asset_id = str(track.get('asset_id') or track.get('uuid') or '').strip() or url

    return {
        'asset_id': asset_id,
        'url': url,
        'title': title,
    }


def validate_background_music_track_url(url: str) -> None:
    """Raise ValueError when the track URL is unusable."""
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https', 'file'}:
        raise ValueError('Track URL must use http, https, or file scheme')

    if parsed.scheme in {'http', 'https'} and not parsed.netloc:
        raise ValueError('Track URL is missing a host')

    lower_path = parsed.path.lower()
    if parsed.scheme == 'file' or lower_path:
        if not any(lower_path.endswith(ext) for ext in SUPPORTED_BACKGROUND_MUSIC_EXTENSIONS):
            raise ValueError(
                'Unsupported background music format. '
                f'Use one of: {", ".join(SUPPORTED_BACKGROUND_MUSIC_EXTENSIONS)}'
            )


def resolve_background_music_url(config: BackgroundMusicConfig) -> str | None:
    track = config.get('track')
    if not track:
        return None
    url = track.get('url')
    return str(url).strip() if url else None


def _clamp_volume(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(DEFAULT_BACKGROUND_MUSIC_CONFIG['volume'])
    return max(0.0, min(1.0, numeric))


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip('/')
    if not path:
        return 'Background track'
    filename = path.rsplit('/', 1)[-1]
    if '.' in filename:
        filename = filename.rsplit('.', 1)[0]
    return filename.replace('_', ' ').replace('-', ' ').title() or 'Background track'
