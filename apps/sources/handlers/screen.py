"""Screen Share Source handler — WebRTC screensharing produce owned by host client."""

from __future__ import annotations

from typing import Any

from apps.sources.handlers.base import SourceHandler
from apps.sources.models import SourceType


class ScreenShareSourceHandler(SourceHandler):
    source_type = SourceType.SCREEN

    def start(self, source_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        return None

    def stop(self, source_id: str, **kwargs: Any) -> None:
        return None
