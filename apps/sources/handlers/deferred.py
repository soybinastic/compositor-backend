"""Deferred SourceType stubs — register so factory is OCP-ready."""

from __future__ import annotations

from typing import Any

from apps.sources.handlers.base import SourceHandler
from apps.sources.models import SourceType


class _StubHandler(SourceHandler):
    def start(self, source_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        raise NotImplementedError(
            f'Source type {self.source_type} is not implemented yet'
        )

    def stop(self, source_id: str, **kwargs: Any) -> None:
        return None


class ImageSourceHandlerStub(_StubHandler):
    source_type = SourceType.IMAGE


class RtmpSourceHandlerStub(_StubHandler):
    """Existing RTMP panel remains; adapter later."""

    source_type = SourceType.RTMP


class AudioSourceHandlerStub(_StubHandler):
    source_type = SourceType.AUDIO


class PdfSourceHandlerStub(_StubHandler):
    source_type = SourceType.PDF
