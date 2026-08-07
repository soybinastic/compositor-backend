"""Pre-recorded video handler — compositor URI decode + SFU produce."""

from __future__ import annotations

from typing import Any

from apps.compositor.commands import (
    AddUriVideoSourceCommand,
    RemoveUriVideoSourceCommand,
    UpdateUriVideoPlaybackCommand,
)
from apps.compositor.worker_manager import get_session_worker_manager
from apps.sources.handlers.base import SourceHandler
from apps.sources.models import SourceType


class PreRecordedVideoHandler(SourceHandler):
    source_type = SourceType.PRERECORDED

    def start(self, source_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        session_id = kwargs['session_id']
        media_url = str(settings.get('mediaUrl') or settings.get('media_url') or '')
        if not media_url:
            raise ValueError('prerecorded source requires settings.mediaUrl')
        display_name = str(
            settings.get('title') or kwargs.get('display_name') or source_id
        )
        worker_manager = get_session_worker_manager()
        worker_manager.send_command(
            AddUriVideoSourceCommand(
                session_id=str(session_id),
                source_id=source_id,
                url=media_url,
                display_name=display_name,
                produce_to_sfu=True,
            )
        )

    def stop(self, source_id: str, **kwargs: Any) -> None:
        session_id = kwargs['session_id']
        worker_manager = get_session_worker_manager()
        worker_manager.send_command(
            RemoveUriVideoSourceCommand(
                session_id=str(session_id),
                source_id=source_id,
            )
        )

    def pause(self, source_id: str, **kwargs: Any) -> None:
        self._playback(source_id, 'pause', **kwargs)

    def resume(self, source_id: str, **kwargs: Any) -> None:
        self._playback(source_id, 'play', **kwargs)

    def seek(self, source_id: str, position_ms: float, **kwargs: Any) -> None:
        self._playback(source_id, 'seek', position_ms=position_ms, **kwargs)

    def apply_playback(self, source_id: str, **kwargs: Any) -> None:
        self._playback(source_id, 'volume', **kwargs)

    def _playback(
        self,
        source_id: str,
        action: str,
        *,
        position_ms: float | None = None,
        **kwargs: Any,
    ) -> None:
        session_id = kwargs['session_id']
        worker_manager = get_session_worker_manager()
        worker_manager.send_command(
            UpdateUriVideoPlaybackCommand(
                session_id=str(session_id),
                source_id=source_id,
                action=action,
                position_ms=position_ms,
                loop=kwargs.get('loop'),
                volume=kwargs.get('volume'),
                muted=kwargs.get('muted'),
            )
        )
