from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.sessions.exceptions import (
    InvalidInviteTokenError,
    SessionEndedError,
    SessionNotFoundError,
)
from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from apps.sessions.services.invite_service import InviteService
from apps.sessions.services.mediasoup_bootstrap import MediasoupMediaPlaneBootstrap
from core.exceptions import MediasoupConnectionError
from core import events
from core.interfaces import IMediaPlaneBootstrap
from core.webhooks import emit_event
from integrations.mediasoup.exceptions import MediasoupApiError


@dataclass(frozen=True)
class SessionCreateResult:
    session: StudioSession
    invite_url: str
    mediasoup_ws_url: str


@dataclass(frozen=True)
class InviteValidationResult:
    session_id: str
    room_id: str
    mediasoup_ws_url: str
    layout: str
    host_display_name: str


class SessionService:
    """Orchestrates studio session lifecycle."""

    def __init__(
        self,
        repository: SessionRepository | None = None,
        invite_service: InviteService | None = None,
        media_plane_bootstrap: IMediaPlaneBootstrap | None = None,
    ) -> None:
        self._repository = repository or SessionRepository()
        self._invite_service = invite_service or InviteService()
        self._media_plane_bootstrap = (
            media_plane_bootstrap or MediasoupMediaPlaneBootstrap()
        )

    def create_session(
        self,
        *,
        host_display_name: str,
        layout: str = LayoutType.CONTAIN,
    ) -> SessionCreateResult:
        invite_token = self._invite_service.generate_token()
        session = self._repository.create(
            host_display_name=host_display_name.strip(),
            invite_token=invite_token,
            layout=layout,
        )

        try:
            session = self._media_plane_bootstrap.bootstrap(session)
        except MediasoupApiError as exc:
            session.delete()
            raise MediasoupConnectionError(str(exc)) from exc

        session.status = SessionStatus.ACTIVE
        self._repository.save(session)

        emit_event(
            events.SESSION_CREATED,
            {
                'session_id': str(session.id),
                'room_id': session.room_id,
                'host_display_name': session.host_display_name,
                'layout': session.layout,
            },
        )

        return SessionCreateResult(
            session=session,
            invite_url=self._invite_service.build_invite_url(session),
            mediasoup_ws_url=self._invite_service.build_mediasoup_ws_url(),
        )

    def get_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self._repository.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(f'Session {session_id} not found')
        return session

    def update_layout(self, session_id: uuid.UUID, layout: str) -> StudioSession:
        session = self.get_session(session_id)
        self._assert_not_ended(session)
        session.layout = layout
        session = self._repository.save(session)

        from apps.compositor.registry import get as get_ingest_manager

        ingest_manager = get_ingest_manager(str(session_id))
        if ingest_manager is not None:
            ingest_manager.set_layout(layout)
            # Layout-only graphics sync: background visibility without rebuild.
            from apps.graphics.service import GraphicsService

            GraphicsService().apply_layout_only(session)

        return session

    def end_session(self, session_id: uuid.UUID) -> StudioSession:
        session = self.get_session(session_id)
        if session.status == SessionStatus.ENDED:
            return session

        from apps.recording.service import RecordingService
        from apps.sources.service import RtmpSourceService
        from apps.streaming.service import StreamingService

        StreamingService().stop_active_stream_if_any(session_id)
        RecordingService().stop_active_recording_if_any(session_id)
        RtmpSourceService().stop_active_sources_if_any(session_id)
        self._media_plane_bootstrap.teardown(session)
        session.end()
        session = self._repository.save(session)

        emit_event(
            events.SESSION_ENDED,
            {
                'session_id': str(session_id),
                'room_id': session.room_id,
            },
        )

        return session

    def validate_invite(
        self,
        session_id: uuid.UUID,
        invite_token: str,
    ) -> InviteValidationResult:
        session = self.get_session(session_id)
        self._assert_not_ended(session)

        if session.invite_token != invite_token:
            raise InvalidInviteTokenError('Invalid invite token')

        return InviteValidationResult(
            session_id=str(session.id),
            room_id=session.room_id,
            mediasoup_ws_url=self._invite_service.build_mediasoup_ws_url(),
            layout=session.layout,
            host_display_name=session.host_display_name,
        )

    @staticmethod
    def _assert_not_ended(session: StudioSession) -> None:
        if session.status == SessionStatus.ENDED:
            raise SessionEndedError('Session has ended')
