"""GStreamer availability check and utilities."""

from __future__ import annotations

from typing import TypedDict


class GStreamerCheckResult(TypedDict):
    available: bool
    version: str | None
    error: str | None


def check_gstreamer() -> GStreamerCheckResult:
    """
    Verify PyGObject and GStreamer are importable and initializable.
    Used by the health endpoint during bootstrap and CI.
    """
    try:
        import gi

        gi.require_version('Gst', '1.0')
        from gi.repository import Gst

        Gst.init(None)
        version = Gst.version_string()

        return {
            'available': True,
            'version': version,
            'error': None,
        }
    except Exception as exc:  # noqa: BLE001 — surface any import/init failure
        return {
            'available': False,
            'version': None,
            'error': str(exc),
        }
