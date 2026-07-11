from __future__ import annotations

import uuid

from apps.sessions.models import LayoutType, SessionStatus, StudioSession


class SessionRepository:
    """Data access for studio sessions."""

    def create(
        self,
        *,
        host_display_name: str,
        invite_token: str,
        layout: str = LayoutType.CONTAIN,
    ) -> StudioSession:
        return StudioSession.objects.create(
            host_display_name=host_display_name,
            invite_token=invite_token,
            layout=layout,
            status=SessionStatus.CREATED,
        )

    def get_by_id(self, session_id: uuid.UUID) -> StudioSession | None:
        return StudioSession.objects.filter(id=session_id).first()

    def save(self, session: StudioSession) -> StudioSession:
        session.save()
        return session
