"""Allocate GPU and RTP port ranges for session workers."""

from __future__ import annotations

import logging

from django.conf import settings

from apps.compositor.worker_manager.redis_ipc import RedisClientProtocol, get_redis_client
from apps.compositor.worker_manager.session_affinity import SessionWorkerAffinity

logger = logging.getLogger(__name__)

GPU_COUNTER_KEY = 'compositor:gpu:counter'
PORT_SLOT_COUNTER_KEY = 'compositor:port:slot'


def allocate_session_resources(
    session_id: str,
    *,
    redis_client: RedisClientProtocol | None = None,
) -> SessionWorkerAffinity:
    client = redis_client or get_redis_client()
    gpu_count = max(1, int(getattr(settings, 'COMPOSITOR_GPU_COUNT', 1)))
    ports_per_session = int(getattr(settings, 'COMPOSITOR_PORTS_PER_SESSION', 20))

    gpu_slot = int(client.incr(GPU_COUNTER_KEY)) - 1
    cuda_device_id = gpu_slot % gpu_count

    port_slot = int(client.incr(PORT_SLOT_COUNTER_KEY)) - 1
    global_min = int(settings.COMPOSITOR_RTP_PORT_MIN)
    global_max = int(settings.COMPOSITOR_RTP_PORT_MAX)
    available = global_max - global_min + 1
    max_sessions = max(1, available // ports_per_session)
    normalized_slot = port_slot % max_sessions
    rtp_port_min = global_min + normalized_slot * ports_per_session
    rtp_port_max = min(global_max, rtp_port_min + ports_per_session - 1)

    affinity = SessionWorkerAffinity(
        session_id=session_id,
        cuda_device_id=cuda_device_id,
        rtp_port_min=rtp_port_min,
        rtp_port_max=rtp_port_max,
    )
    logger.info(
        'Allocated worker resources for session %s (gpu=%s, ports=%s-%s)',
        session_id,
        cuda_device_id,
        rtp_port_min,
        rtp_port_max,
    )
    return affinity
