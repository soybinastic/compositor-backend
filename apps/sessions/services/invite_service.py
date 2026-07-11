from __future__ import annotations

import secrets
from urllib.parse import urlencode, urljoin

from django.conf import settings

from apps.sessions.models import StudioSession


class InviteService:
    """Generates invite tokens and join URLs."""

    def generate_token(self) -> str:
        return secrets.token_urlsafe(32)

    def build_invite_url(self, session: StudioSession) -> str:
        base = settings.STUDIO_FRONTEND_URL.rstrip('/')
        path = f'/join/{session.id}'
        query = urlencode({'token': session.invite_token})
        return f'{urljoin(base + "/", path)}?{query}'

    def build_mediasoup_ws_url(self) -> str:
        return settings.MEDIASOUP_WS_URL.rstrip('/')
