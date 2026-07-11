"""Shared domain exceptions."""


class CompositorError(Exception):
    """Base exception for compositor-backend."""


class GStreamerNotAvailableError(CompositorError):
    """Raised when GStreamer/PyGObject is not available."""


class MediasoupConnectionError(CompositorError):
    """Raised when the mediasoup HTTP API is unreachable."""
