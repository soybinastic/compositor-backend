"""Redis IPC for worker → API lifecycle events."""

from __future__ import annotations

import json
from typing import Any

from apps.compositor.worker_manager.redis_ipc import RedisClientProtocol, get_redis_client

WORKER_EVENTS_KEY = 'compositor:worker:events'


def publish_worker_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    redis_client: RedisClientProtocol | None = None,
) -> None:
    client = redis_client or get_redis_client()
    client.rpush(
        WORKER_EVENTS_KEY,
        json.dumps({'event': event_type, 'payload': payload}),
    )
