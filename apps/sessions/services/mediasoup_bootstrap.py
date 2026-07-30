from __future__ import annotations

import logging

from apps.compositor.worker_manager import get_session_worker_manager
from apps.sessions.models import StudioSession
from apps.sessions.repositories.session_repository import SessionRepository
from core.interfaces import IMediaPlaneBootstrap
from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.exceptions import MediasoupApiError

logger = logging.getLogger(__name__)


class MediasoupMediaPlaneBootstrap(IMediaPlaneBootstrap):
    """
    Bootstraps the mediasoup media plane for a studio session.

    Creates the mediasoup room, registers the compositor BroadcasterPeer,
    and starts the session worker via SessionWorkerManager.
    """

    def __init__(
        self,
        client: MediasoupHttpClient | None = None,
        repository: SessionRepository | None = None,
    ) -> None:
        self._client = client or MediasoupHttpClient()
        self._repository = repository or SessionRepository()
        self._worker_manager = get_session_worker_manager()

    def bootstrap(self, session: StudioSession) -> StudioSession:
        room_id = str(session.id)
        peer_id = f'compositor-{session.id}'

        logger.info('Bootstrapping mediasoup room %s', room_id)
        self._client.create_room(room_id)
        self._client.create_broadcaster(room_id, peer_id)

        session.mediasoup_compositor_peer_id = peer_id
        session = self._repository.save(session)

        self._worker_manager.create_session(session, client=self._client)

        return session

    def teardown(self, session: StudioSession) -> None:
        room_id = str(session.id)
        session_id = str(session.id)

        self._worker_manager.destroy_session(session_id)

        if session.mediasoup_compositor_peer_id:
            try:
                self._client.delete_broadcaster(
                    room_id,
                    session.mediasoup_compositor_peer_id,
                )
            except MediasoupApiError as exc:
                logger.warning(
                    'Failed to delete compositor broadcaster for session %s: %s',
                    session.id,
                    exc,
                )

        try:
            self._client.delete_room(room_id)
        except MediasoupApiError as exc:
            logger.warning(
                'Failed to delete mediasoup room for session %s: %s',
                session.id,
                exc,
            )
