"""SourceHandler registry / factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.sources.models import SourceType

if TYPE_CHECKING:
    from apps.sources.handlers.base import SourceHandler


class SourceHandlerFactory:
    """Open/Closed registry — register new SourceTypes without editing callers."""

    def __init__(self) -> None:
        self._handlers: dict[str, SourceHandler] = {}

    def register(self, handler: SourceHandler) -> None:
        self._handlers[handler.source_type] = handler

    def get(self, source_type: str) -> SourceHandler:
        handler = self._handlers.get(source_type)
        if handler is None:
            raise KeyError(f'No SourceHandler registered for type={source_type}')
        return handler

    def has(self, source_type: str) -> bool:
        return source_type in self._handlers


_factory: SourceHandlerFactory | None = None


def get_source_handler_factory() -> SourceHandlerFactory:
    global _factory
    if _factory is None:
        _factory = SourceHandlerFactory()
        _register_default_handlers(_factory)
    return _factory


def _register_default_handlers(factory: SourceHandlerFactory) -> None:
    from apps.sources.handlers.camera import CameraSourceHandler
    from apps.sources.handlers.deferred import (
        AudioSourceHandlerStub,
        ImageSourceHandlerStub,
        PdfSourceHandlerStub,
        RtmpSourceHandlerStub,
    )
    from apps.sources.handlers.prerecorded import PreRecordedVideoHandler
    from apps.sources.handlers.screen import ScreenShareSourceHandler

    factory.register(CameraSourceHandler())
    factory.register(ScreenShareSourceHandler())
    factory.register(PreRecordedVideoHandler())
    factory.register(ImageSourceHandlerStub())
    factory.register(RtmpSourceHandlerStub())
    factory.register(AudioSourceHandlerStub())
    factory.register(PdfSourceHandlerStub())


SUPPORTED_CREATE_TYPES = frozenset(
    {
        SourceType.CAMERA,
        SourceType.SCREEN,
        SourceType.PRERECORDED,
        SourceType.IMAGE,
        SourceType.RTMP,
        SourceType.AUDIO,
        SourceType.PDF,
    }
)

IMPLEMENTED_RUNTIME_TYPES = frozenset(
    {
        SourceType.CAMERA,
        SourceType.SCREEN,
        SourceType.PRERECORDED,
    }
)
