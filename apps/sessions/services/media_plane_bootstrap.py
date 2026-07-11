from __future__ import annotations

import logging

from apps.sessions.models import StudioSession
from core.interfaces import IMediaPlaneBootstrap

logger = logging.getLogger(__name__)


class NoOpMediaPlaneBootstrap(IMediaPlaneBootstrap):
    """
    Placeholder until Phase 2 wires mediasoup room creation
    and compositor BroadcasterPeer registration.
    """

    def bootstrap(self, session: StudioSession) -> StudioSession:
        logger.info(
            'Media plane bootstrap deferred to Phase 2 for session %s',
            session.id,
        )
        return session

    def teardown(self, session: StudioSession) -> None:
        logger.info(
            'Media plane teardown deferred to Phase 2 for session %s',
            session.id,
        )
