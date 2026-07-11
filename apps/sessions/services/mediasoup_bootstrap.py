from __future__ import annotations

import logging

from apps.compositor.producer_watcher import ProducerWatcher
from apps.compositor.registry import register, unregister
from apps.compositor.session_ingest_manager import SessionIngestManager
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
    and starts RTP ingest polling (Phase 3).
    """

    def __init__(
        self,
        client: MediasoupHttpClient | None = None,
        repository: SessionRepository | None = None,
    ) -> None:
        self._client = client or MediasoupHttpClient()
        self._repository = repository or SessionRepository()

    def bootstrap(self, session: StudioSession) -> StudioSession:
        room_id = str(session.id)
        peer_id = f'compositor-{session.id}'

        logger.info('Bootstrapping mediasoup room %s', room_id)
        self._client.create_room(room_id)
        self._client.create_broadcaster(room_id, peer_id)

        session.mediasoup_compositor_peer_id = peer_id
        session = self._repository.save(session)

        ingest_manager = SessionIngestManager.create(session, client=self._client)
        register(ingest_manager)
        ProducerWatcher.instance().ensure_running()

        return session

    def teardown(self, session: StudioSession) -> None:
        room_id = str(session.id)
        session_id = str(session.id)

        ingest_manager = unregister(session_id)
        if ingest_manager:
            ingest_manager.stop()

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
