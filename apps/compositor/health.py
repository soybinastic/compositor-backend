"""Dependency health checks for the compositor backend."""

from __future__ import annotations

from typing import TypedDict

from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.exceptions import MediasoupApiError


class MediasoupCheckResult(TypedDict):
    available: bool
    error: str | None


def check_mediasoup() -> MediasoupCheckResult:
    """Ping the mediasoup HTTP API. A 404 for a missing room means the server is up."""
    client = MediasoupHttpClient()
    try:
        client._request('GET', '/rooms/__health_probe__')
        return {'available': True, 'error': None}
    except MediasoupApiError as exc:
        if exc.status in (404, 400):
            return {'available': True, 'error': None}
        return {'available': False, 'error': str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {'available': False, 'error': str(exc)}
