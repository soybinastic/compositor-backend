"""Background music runtime orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apps.background_music.exceptions import (
    IngestManagerNotRunningError,
    SceneNotActiveError,
)
from apps.compositor.commands import (
    GetBackgroundMusicStateCommand,
    PauseBackgroundMusicCommand,
    PlayBackgroundMusicCommand,
    ResumeBackgroundMusicCommand,
    SetBackgroundMusicVolumeCommand,
    StopBackgroundMusicCommand,
)
from apps.compositor.worker_manager import get_session_worker_manager
from apps.scenes.exceptions import SceneNotFoundError
from apps.scenes.models import StudioScene
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sessions.models import SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository

_VALID_REJECTION_REASONS = frozenset(
    {
        'no_track_loaded',
        'unsupported_format',
        'file_missing',
        'decode_failed',
        'already_playing',
        'backend_unavailable',
        'playback_timeout',
        'scene_not_active',
    }
)


@dataclass(frozen=True)
class BackgroundMusicCommandAck:
    accepted: bool
    state: dict[str, Any]
    rejection_reason: str | None = None


class BackgroundMusicService:
    """Poll and transport control for compositor-owned background music."""

    def __init__(self, repository: SessionRepository | None = None) -> None:
        self._repository = repository or SessionRepository()

    def get_runtime_state(self, session_id: uuid.UUID) -> dict[str, Any]:
        session = self._get_active_session(session_id)
        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session_id)):
            return self._idle_runtime_state(session)

        result = worker_manager.send_command(
            GetBackgroundMusicStateCommand(session_id=str(session_id))
        )
        return result.data

    def play(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
    ) -> BackgroundMusicCommandAck:
        return self._transport(
            session_id,
            scene_id,
            lambda sid, sid_str: PlayBackgroundMusicCommand(
                session_id=sid,
                scene_id=sid_str,
            ),
        )

    def pause(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
    ) -> BackgroundMusicCommandAck:
        return self._transport(
            session_id,
            scene_id,
            lambda sid, sid_str: PauseBackgroundMusicCommand(
                session_id=sid,
                scene_id=sid_str,
            ),
        )

    def resume(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
    ) -> BackgroundMusicCommandAck:
        return self._transport(
            session_id,
            scene_id,
            lambda sid, sid_str: ResumeBackgroundMusicCommand(
                session_id=sid,
                scene_id=sid_str,
            ),
        )

    def stop(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
    ) -> BackgroundMusicCommandAck:
        return self._transport(
            session_id,
            scene_id,
            lambda sid, sid_str: StopBackgroundMusicCommand(
                session_id=sid,
                scene_id=sid_str,
            ),
        )

    def set_volume(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        *,
        volume: float,
        muted: bool | None = None,
    ) -> BackgroundMusicCommandAck:
        return self._transport(
            session_id,
            scene_id,
            lambda sid, sid_str: SetBackgroundMusicVolumeCommand(
                session_id=sid,
                scene_id=sid_str,
                volume=volume,
                muted=muted,
            ),
        )

    def _transport(
        self,
        session_id: uuid.UUID,
        scene_id: uuid.UUID,
        command_factory,
    ) -> BackgroundMusicCommandAck:
        session = self._get_active_session(session_id)
        self._assert_scene_belongs_to_session(session, scene_id)
        self._assert_active_scene(session, scene_id)

        worker_manager = get_session_worker_manager()
        if not worker_manager.is_running(str(session_id)):
            raise IngestManagerNotRunningError(
                'Compositor ingest is not running for this session'
            )

        try:
            result = worker_manager.send_command(
                command_factory(str(session_id), str(scene_id))
            )
            return BackgroundMusicCommandAck(
                accepted=True,
                state=result.data,
            )
        except ValueError as exc:
            reason = self._normalize_rejection_reason(exc)
            state = self.get_runtime_state(session_id)
            return BackgroundMusicCommandAck(
                accepted=False,
                state=state,
                rejection_reason=reason,
            )

    def _get_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._repository.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(f'Session {session_id} not found')
        return session

    def _get_active_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._get_session(session_id)
        if session.status == SessionStatus.ENDED:
            raise SessionEndedError('Session has ended')
        return session

    @staticmethod
    def _assert_scene_belongs_to_session(
        session: StudioSession,
        scene_id: uuid.UUID,
    ) -> None:
        if not StudioScene.objects.filter(session=session, id=scene_id).exists():
            raise SceneNotFoundError(f'Scene {scene_id} not found')

    @staticmethod
    def _assert_active_scene(session: StudioSession, scene_id: uuid.UUID) -> None:
        if session.active_scene_id is None or str(session.active_scene_id) != str(
            scene_id
        ):
            raise SceneNotActiveError('Only the active scene may control playback')

    @staticmethod
    def _idle_runtime_state(session: StudioSession) -> dict[str, Any]:
        scene_id = str(session.active_scene_id) if session.active_scene_id else None
        return {
            'scene_id': scene_id,
            'playback_state': 'idle',
            'position_ms': 0,
            'duration_ms': 0,
            'error': None,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _normalize_rejection_reason(exc: ValueError) -> str:
        if not exc.args:
            return 'backend_unavailable'
        reason = str(exc.args[0])
        if reason in _VALID_REJECTION_REASONS:
            return reason
        return 'backend_unavailable'
