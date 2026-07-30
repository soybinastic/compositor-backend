"""Redis channels and helpers for API ↔ worker IPC."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

from django.conf import settings

logger = logging.getLogger(__name__)

SUPERVISOR_CONTROL_KEY = 'compositor:supervisor:control'


def session_commands_key(session_id: str) -> str:
    return f'compositor:session:{session_id}:commands'


def session_reply_key(session_id: str, command_id: str) -> str:
    return f'compositor:session:{session_id}:reply:{command_id}'


def session_ready_key(session_id: str) -> str:
    return f'compositor:session:{session_id}:ready'


class RedisClientProtocol(Protocol):
    def rpush(self, key: str, value: str) -> int: ...

    def blpop(self, keys: list[str], timeout: int = 0) -> tuple[str, str] | None: ...

    def get(self, key: str) -> str | bytes | None: ...

    def set(self, key: str, value: str, ex: int | None = None) -> bool: ...

    def delete(self, *keys: str) -> int: ...

    def incr(self, key: str) -> int: ...


def get_redis_client() -> RedisClientProtocol:
    import redis

    url = getattr(settings, 'COMPOSITOR_REDIS_URL', 'redis://127.0.0.1:6379/0')
    return redis.Redis.from_url(url, decode_responses=True)


def publish_supervisor_control(client: RedisClientProtocol, payload: dict[str, Any]) -> None:
    client.rpush(SUPERVISOR_CONTROL_KEY, json.dumps(payload))


def enqueue_session_command(
    client: RedisClientProtocol,
    session_id: str,
    payload: dict[str, Any],
) -> None:
    client.rpush(session_commands_key(session_id), json.dumps(payload))


def wait_for_session_ready(
    client: RedisClientProtocol,
    session_id: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float = 0.1,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    ready_key = session_ready_key(session_id)
    while time.monotonic() < deadline:
        if client.get(ready_key):
            return True
        time.sleep(poll_interval_sec)
    return False


def wait_for_command_reply(
    client: RedisClientProtocol,
    session_id: str,
    command_id: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float = 0.05,
) -> dict[str, Any] | None:
    reply_key = session_reply_key(session_id, command_id)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        raw = client.get(reply_key)
        if raw:
            client.delete(reply_key)
            return json.loads(raw)
        time.sleep(poll_interval_sec)
    return None


def mark_session_ready(client: RedisClientProtocol, session_id: str) -> None:
    client.set(session_ready_key(session_id), '1')


def clear_session_ready(client: RedisClientProtocol, session_id: str) -> None:
    client.delete(session_ready_key(session_id))
