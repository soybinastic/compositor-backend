import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.compositor.worker_manager.media_supervisor import MediaSupervisor, WorkerProcess
from apps.compositor.worker_manager.resource_allocator import allocate_session_resources
from apps.compositor.worker_manager.session_affinity import (
    SessionWorkerAffinity,
    clear_session_affinity,
    get_session_affinity,
    increment_restart_count,
    is_session_expected,
    mark_session_unexpected,
    register_session_affinity,
)
from apps.compositor.worker_manager.worker_heartbeat import (
    is_session_heartbeat_alive,
    session_heartbeat_key,
    touch_session_heartbeat,
)


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def blpop(self, keys: list[str], timeout: int = 0):
        del timeout
        for key in keys:
            items = self.lists.get(key, [])
            if items:
                return key, items.pop(0)
        return None

    def get(self, key: str):
        return self.kv.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.kv[key] = value
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.kv:
                del self.kv[key]
                removed += 1
        return removed

    def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


class SessionAffinityTests(TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.session_id = str(uuid.uuid4())

    def test_register_and_read_affinity(self):
        affinity = SessionWorkerAffinity(
            session_id=self.session_id,
            cuda_device_id=2,
            rtp_port_min=50100,
            rtp_port_max=50119,
        )
        register_session_affinity(self.session_id, affinity, redis_client=self.redis)

        loaded = get_session_affinity(self.session_id, redis_client=self.redis)
        assert loaded is not None
        self.assertEqual(loaded.cuda_device_id, 2)
        self.assertTrue(is_session_expected(self.session_id, redis_client=self.redis))

    def test_mark_unexpected_clears_running_state(self):
        affinity = SessionWorkerAffinity(
            session_id=self.session_id,
            cuda_device_id=0,
            rtp_port_min=50000,
            rtp_port_max=50019,
        )
        register_session_affinity(self.session_id, affinity, redis_client=self.redis)
        mark_session_unexpected(self.session_id, redis_client=self.redis)
        self.assertFalse(is_session_expected(self.session_id, redis_client=self.redis))

    def test_increment_restart_count(self):
        affinity = SessionWorkerAffinity(
            session_id=self.session_id,
            cuda_device_id=0,
            rtp_port_min=50000,
            rtp_port_max=50019,
        )
        register_session_affinity(self.session_id, affinity, redis_client=self.redis)
        updated = increment_restart_count(self.session_id, redis_client=self.redis)
        assert updated is not None
        self.assertEqual(updated.restart_count, 1)


class ResourceAllocatorTests(TestCase):
    @override_settings(
        COMPOSITOR_GPU_COUNT=4,
        COMPOSITOR_PORTS_PER_SESSION=20,
        COMPOSITOR_RTP_PORT_MIN=50000,
        COMPOSITOR_RTP_PORT_MAX=50999,
    )
    def test_allocate_resources_assigns_gpu_and_ports(self):
        redis = FakeRedis()
        session_id = str(uuid.uuid4())
        first = allocate_session_resources(session_id, redis_client=redis)
        second = allocate_session_resources(str(uuid.uuid4()), redis_client=redis)

        self.assertEqual(first.cuda_device_id, 0)
        self.assertEqual(second.cuda_device_id, 1)
        self.assertEqual(first.rtp_port_min, 50000)
        self.assertEqual(first.rtp_port_max, 50019)
        self.assertEqual(second.rtp_port_min, 50020)


class WorkerHeartbeatTests(TestCase):
    def test_touch_and_check_heartbeat(self):
        redis = FakeRedis()
        session_id = str(uuid.uuid4())
        self.assertFalse(is_session_heartbeat_alive(session_id, redis_client=redis))
        touch_session_heartbeat(session_id, redis_client=redis)
        self.assertTrue(is_session_heartbeat_alive(session_id, redis_client=redis))
        self.assertIn(session_heartbeat_key(session_id), redis.kv)


class MediaSupervisorRestartTests(TestCase):
    @patch('apps.compositor.worker_manager.media_supervisor.time.sleep')
    @patch.object(MediaSupervisor, '_spawn_worker')
    def test_restarts_expected_worker_after_crash(self, mock_spawn, _mock_sleep):
        redis = FakeRedis()
        session_id = str(uuid.uuid4())
        affinity = SessionWorkerAffinity(
            session_id=session_id,
            cuda_device_id=1,
            rtp_port_min=50000,
            rtp_port_max=50019,
            restart_count=0,
        )
        register_session_affinity(session_id, affinity, redis_client=redis)

        supervisor = MediaSupervisor(redis_client=redis)
        supervisor._handle_worker_failure(session_id, reason='test crash')

        mock_spawn.assert_called_once_with(session_id)

    @patch('apps.compositor.worker_manager.media_supervisor.time.sleep')
    @patch.object(MediaSupervisor, '_spawn_worker')
    def test_stops_restart_after_max_attempts(self, mock_spawn, _mock_sleep):
        redis = FakeRedis()
        session_id = str(uuid.uuid4())
        affinity = SessionWorkerAffinity(
            session_id=session_id,
            cuda_device_id=1,
            rtp_port_min=50000,
            rtp_port_max=50019,
            restart_count=3,
        )
        register_session_affinity(session_id, affinity, redis_client=redis)

        supervisor = MediaSupervisor(redis_client=redis)
        supervisor._max_restarts = 3
        supervisor._handle_worker_failure(session_id, reason='test crash')

        mock_spawn.assert_not_called()
        self.assertFalse(is_session_expected(session_id, redis_client=redis))
