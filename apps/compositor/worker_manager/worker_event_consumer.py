"""Consume worker lifecycle events on the API process."""

from __future__ import annotations

import json
import logging
import threading

from apps.compositor.worker_manager.redis_ipc import RedisClientProtocol, get_redis_client
from apps.compositor.worker_manager.worker_event_ipc import WORKER_EVENTS_KEY
from core.worker_event_dispatch import dispatch_worker_event

logger = logging.getLogger(__name__)


class WorkerEventConsumer:
    """Background thread that drains worker events from Redis."""

    _instance: WorkerEventConsumer | None = None
    _instance_lock = threading.Lock()

    def __init__(self, redis_client: RedisClientProtocol | None = None) -> None:
        self._redis = redis_client or get_redis_client()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def instance(cls) -> WorkerEventConsumer:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = WorkerEventConsumer()
            return cls._instance

    def ensure_running(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name='worker-event-consumer',
            daemon=True,
        )
        self._thread.start()
        logger.info('Worker event consumer started')

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._redis.blpop([WORKER_EVENTS_KEY], timeout=1)
            except Exception:
                logger.exception('Worker event consumer Redis read failed')
                continue
            if item is None:
                continue
            self._handle_message(item[1])

    def _handle_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
            event_type = message['event']
            payload = message.get('payload', {})
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning('Ignoring invalid worker event payload: %s', raw)
            return

        try:
            dispatch_worker_event(event_type, payload)
        except Exception:
            logger.exception('Failed to dispatch worker event %s', event_type)


def ensure_worker_event_consumer_running() -> None:
    WorkerEventConsumer.instance().ensure_running()
