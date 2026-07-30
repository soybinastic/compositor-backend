"""Redis-backed session worker affinity and resource assignment."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.compositor.worker_manager.redis_ipc import RedisClientProtocol, get_redis_client

logger = logging.getLogger(__name__)


def session_affinity_key(session_id: str) -> str:
    return f'compositor:session:{session_id}:affinity'


@dataclass(frozen=True)
class SessionWorkerAffinity:
    session_id: str
    cuda_device_id: int
    rtp_port_min: int
    rtp_port_max: int
    expected: bool = True
    restart_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'cuda_device_id': self.cuda_device_id,
            'rtp_port_min': self.rtp_port_min,
            'rtp_port_max': self.rtp_port_max,
            'expected': self.expected,
            'restart_count': self.restart_count,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionWorkerAffinity:
        return cls(
            session_id=str(payload['session_id']),
            cuda_device_id=int(payload['cuda_device_id']),
            rtp_port_min=int(payload['rtp_port_min']),
            rtp_port_max=int(payload['rtp_port_max']),
            expected=bool(payload.get('expected', True)),
            restart_count=int(payload.get('restart_count', 0)),
        )


def register_session_affinity(
    session_id: str,
    affinity: SessionWorkerAffinity,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> None:
    client = redis_client or get_redis_client()
    client.set(session_affinity_key(session_id), json.dumps(affinity.to_dict()))


def get_session_affinity(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> SessionWorkerAffinity | None:
    client = redis_client or get_redis_client()
    raw = client.get(session_affinity_key(session_id))
    if not raw:
        return None
    try:
        return SessionWorkerAffinity.from_dict(json.loads(raw))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning('Invalid session affinity for %s: %s', session_id, raw)
        return None


def clear_session_affinity(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> None:
    client = redis_client or get_redis_client()
    client.delete(session_affinity_key(session_id))


def is_session_expected(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> bool:
    affinity = get_session_affinity(session_id, redis_client=redis_client)
    return affinity is not None and affinity.expected


def increment_restart_count(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> SessionWorkerAffinity | None:
    affinity = get_session_affinity(session_id, redis_client=redis_client)
    if affinity is None:
        return None
    updated = SessionWorkerAffinity(
        session_id=affinity.session_id,
        cuda_device_id=affinity.cuda_device_id,
        rtp_port_min=affinity.rtp_port_min,
        rtp_port_max=affinity.rtp_port_max,
        expected=affinity.expected,
        restart_count=affinity.restart_count + 1,
    )
    register_session_affinity(session_id, updated, redis_client=redis_client)
    return updated


def mark_session_unexpected(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> None:
    affinity = get_session_affinity(session_id, redis_client=redis_client)
    if affinity is None:
        return
    updated = SessionWorkerAffinity(
        session_id=affinity.session_id,
        cuda_device_id=affinity.cuda_device_id,
        rtp_port_min=affinity.rtp_port_min,
        rtp_port_max=affinity.rtp_port_max,
        expected=False,
        restart_count=affinity.restart_count,
    )
    register_session_affinity(session_id, updated, redis_client=redis_client)
