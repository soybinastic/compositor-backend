"""Session worker heartbeat over Redis."""

from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

from apps.compositor.worker_manager.redis_ipc import RedisClientProtocol, get_redis_client

logger = logging.getLogger(__name__)


def session_heartbeat_key(session_id: str) -> str:
    return f'compositor:session:{session_id}:heartbeat'


def touch_session_heartbeat(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> None:
    client = redis_client or get_redis_client()
    ttl = int(getattr(settings, 'SESSION_WORKER_HEARTBEAT_TTL_SEC', 15))
    client.set(session_heartbeat_key(session_id), str(time.time()), ex=ttl)


def clear_session_heartbeat(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> None:
    client = redis_client or get_redis_client()
    client.delete(session_heartbeat_key(session_id))


def is_session_heartbeat_alive(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> bool:
    client = redis_client or get_redis_client()
    return client.get(session_heartbeat_key(session_id)) is not None


class SessionWorkerHeartbeat:
    """Refreshes a Redis heartbeat key until stopped."""

    def __init__(
        self,
        session_id: str,
        *,
        redis_client: RedisClientProtocol | None = None,
        interval_sec: float | None = None,
    ) -> None:
        self.session_id = session_id
        self._redis = redis_client or get_redis_client()
        self._interval = (
            interval_sec
            if interval_sec is not None
            else float(getattr(settings, 'SESSION_WORKER_HEARTBEAT_INTERVAL_SEC', 5))
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        touch_session_heartbeat(self.session_id, redis_client=self._redis)
        self._thread = threading.Thread(
            target=self._run,
            name=f'worker-heartbeat-{self.session_id[:8]}',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
            self._thread = None
        clear_session_heartbeat(self.session_id, redis_client=self._redis)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                touch_session_heartbeat(self.session_id, redis_client=self._redis)
            except Exception:
                logger.exception(
                    'Failed to refresh heartbeat for session %s',
                    self.session_id,
                )
