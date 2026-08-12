"""Source handler strategy interfaces (SOLID / OCP)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class CompositorIngestPort(Protocol):
    def add_uri_video_source(
        self,
        source_id: str,
        *,
        url: str,
        display_name: str = '',
        produce_to_sfu: bool = True,
    ) -> Any: ...

    def remove_uri_video_source(self, source_id: str) -> None: ...

    def update_uri_video_playback(
        self,
        source_id: str,
        *,
        action: str,
        position_ms: float | None = None,
        loop: bool | None = None,
        volume: float | None = None,
        muted: bool | None = None,
    ) -> None: ...


class SourceHandler(ABC):
    """Strategy for a SourceType lifecycle."""

    source_type: str

    @abstractmethod
    def start(self, source_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self, source_id: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def pause(self, source_id: str, **kwargs: Any) -> None:
        """Optional — playback sources override."""

    def resume(self, source_id: str, **kwargs: Any) -> None:
        """Optional — playback sources override."""

    def seek(self, source_id: str, position_ms: float, **kwargs: Any) -> None:
        """Optional — playback sources override."""

    def apply_settings(self, source_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        """Optional settings patch."""


class PlaybackControllable(Protocol):
    def pause(self, source_id: str, **kwargs: Any) -> None: ...

    def resume(self, source_id: str, **kwargs: Any) -> None: ...

    def seek(self, source_id: str, position_ms: float, **kwargs: Any) -> None: ...
