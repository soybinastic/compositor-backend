import json
import uuid
from unittest.mock import MagicMock

from django.test import TestCase, override_settings

from apps.compositor.commands import ChangeLayoutCommand, CommandResult, GetStatusCommand
from apps.compositor.worker_manager.in_process import InProcessSessionWorkerRuntime
from apps.compositor.worker_manager.redis_ipc import (
    session_commands_key,
    session_ready_key,
    session_reply_key,
)
from apps.compositor.worker_manager.runtime_factory import create_session_worker_runtime
from apps.compositor.worker_manager.supervisor_runtime import SupervisorSessionWorkerRuntime


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
                value = items.pop(0)
                return key, value
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


class RuntimeFactoryTests(TestCase):
    @override_settings(COMPOSITOR_WORKER_MODE='inprocess')
    def test_factory_defaults_to_inprocess(self):
        runtime = create_session_worker_runtime()
        self.assertIsInstance(runtime, InProcessSessionWorkerRuntime)

    @override_settings(COMPOSITOR_WORKER_MODE='supervisor')
    def test_factory_selects_supervisor(self):
        runtime = create_session_worker_runtime()
        self.assertIsInstance(runtime, SupervisorSessionWorkerRuntime)


class SupervisorRuntimeTests(TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.runtime = SupervisorSessionWorkerRuntime(redis_client=self.redis)
        self.session_id = str(uuid.uuid4())

    def _make_session(self):
        session = MagicMock()
        session.id = self.session_id
        return session

    def test_create_session_publishes_spawn_and_waits_for_ready(self):
        session = self._make_session()

        def mark_ready():
            self.redis.kv[session_ready_key(self.session_id)] = '1'

        import threading

        timer = threading.Timer(0.05, mark_ready)
        timer.start()
        try:
            self.runtime.create_session(session)
        finally:
            timer.cancel()

        control_messages = self.redis.lists.get('compositor:supervisor:control', [])
        self.assertEqual(len(control_messages), 1)
        self.assertEqual(json.loads(control_messages[0])['action'], 'spawn')
        self.assertTrue(self.runtime.is_running(self.session_id))

    def test_send_command_enqueues_and_waits_for_reply(self):
        from apps.compositor.worker_manager.session_affinity import (
            SessionWorkerAffinity,
            register_session_affinity,
        )

        register_session_affinity(
            self.session_id,
            SessionWorkerAffinity(
                session_id=self.session_id,
                cuda_device_id=0,
                rtp_port_min=50000,
                rtp_port_max=50019,
            ),
            redis_client=self.redis,
        )
        self.runtime._running.add(self.session_id)
        command = ChangeLayoutCommand(session_id=self.session_id, layout='GRID')

        def publish_reply():
            payload = {
                'command_id': command.command_id,
                'success': True,
                'data': None,
                'error': None,
            }
            self.redis.kv[session_reply_key(self.session_id, command.command_id)] = json.dumps(
                payload
            )

        import threading

        timer = threading.Timer(0.05, publish_reply)
        timer.start()
        try:
            result = self.runtime.send_command(command)
        finally:
            timer.cancel()

        self.assertTrue(result.success)
        queued = self.redis.lists[session_commands_key(self.session_id)]
        self.assertEqual(json.loads(queued[0])['action'], 'command')

    def test_destroy_session_publishes_destroy(self):
        self.runtime._running.add(self.session_id)
        self.runtime.destroy_session(self.session_id)
        self.assertFalse(self.runtime.is_running(self.session_id))
        messages = self.redis.lists.get('compositor:supervisor:control', [])
        self.assertEqual(json.loads(messages[-1])['action'], 'destroy')

    def test_get_status_decodes_session_status(self):
        from apps.compositor.worker_manager.session_affinity import (
            SessionWorkerAffinity,
            register_session_affinity,
        )

        register_session_affinity(
            self.session_id,
            SessionWorkerAffinity(
                session_id=self.session_id,
                cuda_device_id=0,
                rtp_port_min=50000,
                rtp_port_max=50019,
            ),
            redis_client=self.redis,
        )
        self.runtime._running.add(self.session_id)
        status_payload = {
            '_type': 'SessionIngestStatus',
            'session_id': self.session_id,
            'room_id': self.session_id,
            'compositor_peer_id': 'compositor-test',
            'layout': 'CONTAIN',
            'joined': False,
            'composited_frames': 0,
            'canvas_width': 1920,
            'canvas_height': 1080,
            'host_peer_id': None,
            'recording_active': False,
            'recording_file_path': None,
            'streaming_active': False,
            'streaming_destination_type': None,
            'streaming_destination_url': None,
            'video_backend': 'cpu',
            'requested_video_backend': 'cpu',
            'participants': [],
            'rtmp_sources': [],
        }
        command = GetStatusCommand(session_id=self.session_id)

        def publish_reply():
            from apps.compositor.worker_manager.command_codec import encode_result

            payload = CommandResult.ok(command.command_id, data=status_payload)
            self.redis.kv[session_reply_key(self.session_id, command.command_id)] = json.dumps(
                encode_result(payload)
            )

        import threading

        timer = threading.Timer(0.05, publish_reply)
        timer.start()
        try:
            result = self.runtime.send_command(command)
        finally:
            timer.cancel()

        self.assertTrue(result.success)
        self.assertIsNotNone(result.data)
        self.assertEqual(result.data.session_id, self.session_id)
