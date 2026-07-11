"""Polls mediasoup for new/removed producers and syncs RTP ingest."""

from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

from apps.compositor.registry import all_managers
from integrations.mediasoup.client import MediasoupHttpClient
from integrations.mediasoup.exceptions import MediasoupApiError

logger = logging.getLogger(__name__)


class ProducerWatcher:
    """
    Background poller that keeps session ingest in sync with mediasoup producers.

    One daemon thread polls all active sessions every PRODUCER_POLL_INTERVAL seconds.
    """

    _instance: ProducerWatcher | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._client = MediasoupHttpClient()
        self._interval = settings.PRODUCER_POLL_INTERVAL
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def instance(cls) -> ProducerWatcher:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = ProducerWatcher()
            return cls._instance

    def ensure_running(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name='producer-watcher',
            daemon=True,
        )
        self._thread.start()
        logger.info('ProducerWatcher started (interval=%ss)', self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            managers = all_managers()
            if not managers:
                continue

            for manager in managers:
                self._poll_session(manager)

    def _poll_session(self, manager) -> None:
        try:
            response = self._client.get_producers(manager.room_id)
            peer_producers_infos = response.get('peerProducersInfos', [])
            manager.sync_producers(peer_producers_infos)
        except MediasoupApiError as exc:
            logger.warning(
                'Producer poll failed for session %s: %s',
                manager.session_id,
                exc,
            )
        except Exception:
            logger.exception(
                'Unexpected error polling producers for session %s',
                manager.session_id,
            )
