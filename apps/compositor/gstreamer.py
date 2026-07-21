"""GStreamer availability check and utilities."""

from __future__ import annotations

from typing import TypedDict

from django.conf import settings


class GStreamerCheckResult(TypedDict):
    available: bool
    version: str | None
    error: str | None
    requested_backend: str | None
    resolved_backend: str | None
    elements: dict[str, bool] | None


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

        from apps.compositor.video_mix_backend import (
            probe_backend_elements,
            resolve_video_backend,
        )

        requested = settings.COMPOSITOR_VIDEO_BACKEND
        elements = probe_backend_elements()
        try:
            resolved = resolve_video_backend(
                requested,
                cuda_device_id=settings.COMPOSITOR_CUDA_DEVICE_ID,
            )
        except Exception as resolve_exc:  # noqa: BLE001
            return {
                'available': False,
                'version': version,
                'error': str(resolve_exc),
                'requested_backend': requested,
                'resolved_backend': None,
                'elements': elements,
            }

        return {
            'available': True,
            'version': version,
            'error': None,
            'requested_backend': requested,
            'resolved_backend': resolved,
            'elements': elements,
        }
    except Exception as exc:  # noqa: BLE001 — surface any import/init failure
        return {
            'available': False,
            'version': None,
            'error': str(exc),
            'requested_backend': getattr(settings, 'COMPOSITOR_VIDEO_BACKEND', None),
            'resolved_backend': None,
            'elements': None,
        }
