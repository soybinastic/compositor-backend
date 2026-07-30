"""Per-session mediasoup producer polling for worker processes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from django.conf import settings

from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.exceptions import MediasoupApiError

logger = logging.getLogger(__name__)

SyncProducersCallback = Callable[[list[dict[str, Any]]], None]


class SessionProducerPoller:
    """
    Polls mediasoup for one session and invokes sync_producers on the worker.

    Step 5: each session worker owns its poller (in-process thread or subprocess).
    """

    def __init__(
        self,
        session_id: str,
        room_id: str,
        *,
        sync_producers: SyncProducersCallback,
        client: MediasoupHttpClient | None = None,
        interval_sec: float | None = None,
    ) -> None:
        self.session_id = session_id
        self.room_id = room_id
        self._sync_producers = sync_producers
        self._client = client or MediasoupHttpClient()
        self._interval = (
            interval_sec
            if interval_sec is not None
            else float(getattr(settings, 'PRODUCER_POLL_INTERVAL', 2))
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f'producer-poller-{self.session_id[:8]}',
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            'Started producer poller for session %s (interval=%ss)',
            self.session_id,
            self._interval,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self._poll_once()

    def _poll_once(self) -> None:
        try:
            response = self._client.get_producers(self.room_id)
            peer_producers_infos = response.get('peerProducersInfos', [])
            self._sync_producers(peer_producers_infos)
        except MediasoupApiError as exc:
            logger.warning(
                'Producer poll failed for session %s: %s',
                self.session_id,
                exc,
            )
        except Exception:
            logger.exception(
                'Unexpected error polling producers for session %s',
                self.session_id,
            )


class SessionProducerPollerRegistry:
    """Tracks active per-session producer pollers."""

    def __init__(self) -> None:
        self._pollers: dict[str, SessionProducerPoller] = {}
        self._lock = threading.Lock()

    def attach(self, session_id: str, poller: SessionProducerPoller) -> None:
        with self._lock:
            self._pollers[session_id] = poller

    def pop(self, session_id: str) -> SessionProducerPoller | None:
        with self._lock:
            return self._pollers.pop(session_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._pollers)

    def shutdown_all(self) -> None:
        with self._lock:
            pollers = list(self._pollers.values())
            self._pollers.clear()
        for poller in pollers:
            poller.stop()


_poller_registry = SessionProducerPollerRegistry()


def get_poller_registry() -> SessionProducerPollerRegistry:
    return _poller_registry


def start_session_producer_poller(
    session_id: str,
    room_id: str,
    *,
    sync_producers: SyncProducersCallback,
    client: MediasoupHttpClient | None = None,
) -> SessionProducerPoller:
    """Create, register, and start a poller for one session worker."""
    registry = get_poller_registry()
    existing = registry.pop(session_id)
    if existing is not None:
        existing.stop()

    poller = SessionProducerPoller(
        session_id,
        room_id,
        sync_producers=sync_producers,
        client=client,
    )
    registry.attach(session_id, poller)
    poller.start()
    return poller


def stop_session_producer_poller(session_id: str) -> None:
    poller = get_poller_registry().pop(session_id)
    if poller is not None:
        poller.stop()
