"""Camera Source handler — WebRTC produce is owned by the host client."""

from __future__ import annotations

from typing import Any

from apps.sources.handlers.base import SourceHandler
from apps.sources.models import SourceType


class CameraSourceHandler(SourceHandler):
    """Camera media is published by the host RoomClient; registry tracks metadata."""

    source_type = SourceType.CAMERA

    def start(self, source_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        # Host FE produces mediasoup video with appData.sourceId.
        return None

    def stop(self, source_id: str, **kwargs: Any) -> None:
        return None
