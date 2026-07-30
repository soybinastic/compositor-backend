import threading
import time
import uuid
from unittest.mock import MagicMock

from django.test import TestCase

from apps.compositor.commands import (
    ChangeLayoutCommand,
    GetStatusCommand,
    StartRecordingCommand,
    StopRecordingCommand,
)
from apps.compositor.registry import register, unregister
from apps.compositor.session_producer_poller import stop_session_producer_poller
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.worker_manager.command_executor import get_executor_registry
from apps.compositor.worker_manager.in_process import InProcessSessionWorkerRuntime
from apps.compositor.worker_manager.runtime_factory import create_session_worker_runtime
from apps.compositor.worker_manager.session_worker_manager import SessionWorkerManager


class SessionWorkerManagerTests(TestCase):
    def setUp(self):
        self.runtime = InProcessSessionWorkerRuntime()
        self.manager = SessionWorkerManager(runtime=self.runtime)
        self.session_id = str(uuid.uuid4())

    def tearDown(self):
        stop_session_producer_poller(self.session_id)
        executor = get_executor_registry().pop(self.session_id)
        if executor is not None:
            executor.shutdown()
        unregister(self.session_id)

    def _register_mock_manager(self):
        mock_pipeline = MagicMock()
        mock_pipeline.is_recording.return_value = False
        mock_pipeline.is_streaming.return_value = False
        mock_pipeline.get_status.return_value = MagicMock(layout='CONTAIN')
        manager = SessionIngestManager(
            session_id=self.session_id,
            room_id=self.session_id,
            compositor_peer_id='compositor-test',
            layout='CONTAIN',
            consumer_service=MagicMock(),
            compositor_pipeline=mock_pipeline,
        )
        register(manager)
        get_executor_registry().attach(self.session_id, manager)
        return manager, mock_pipeline

    def test_is_running_false_when_unregistered(self):
        self.assertFalse(self.manager.is_running(self.session_id))

    def test_is_running_true_when_registered(self):
        self._register_mock_manager()
        self.assertTrue(self.manager.is_running(self.session_id))

    def test_send_command_change_layout(self):
        manager, mock_pipeline = self._register_mock_manager()

        self.manager.send_command(
            ChangeLayoutCommand(
                session_id=self.session_id,
                layout='GRID',
                graphics_state={'background': None},
            )
        )

        mock_pipeline.set_layout.assert_called_once_with(
            'GRID',
            graphics_state={'background': None},
        )
        self.assertEqual(manager.layout, 'GRID')

    def test_send_command_start_recording(self):
        _, mock_pipeline = self._register_mock_manager()

        self.manager.send_command(
            StartRecordingCommand(
                session_id=self.session_id,
                file_path='/tmp/test.mp4',
            )
        )

        mock_pipeline.start_recording.assert_called_once()

    def test_send_command_stop_recording(self):
        _, mock_pipeline = self._register_mock_manager()
        mock_pipeline.stop_recording.return_value = '/tmp/test.mp4'

        result = self.manager.send_command(
            StopRecordingCommand(session_id=self.session_id)
        )

        mock_pipeline.stop_recording.assert_called_once()
        self.assertEqual(result.data, '/tmp/test.mp4')

    def test_get_status_returns_snapshot(self):
        manager, mock_pipeline = self._register_mock_manager()
        mock_pipeline.get_status.return_value = MagicMock(
            layout='CONTAIN',
            canvas_width=1920,
            canvas_height=1080,
            composited_frames=10,
            host_peer_id=None,
            recording_active=False,
            recording_file_path=None,
            streaming_active=False,
            streaming_destination_type=None,
            streaming_destination_url=None,
            video_backend='cpu',
            requested_video_backend='cpu',
        )
        manager._consumer_service.joined = False

        status = self.manager.get_status(self.session_id)

        self.assertIsNotNone(status)
        self.assertEqual(status.session_id, self.session_id)

    def test_send_command_raises_when_worker_missing(self):
        with self.assertRaises(LookupError):
            self.manager.send_command(GetStatusCommand(session_id=self.session_id))

    def test_destroy_session_unregisters_worker(self):
        _, mock_pipeline = self._register_mock_manager()
        self.manager.destroy_session(self.session_id)
        mock_pipeline.stop.assert_called_once()
        self.assertFalse(self.manager.is_running(self.session_id))

    def test_commands_execute_serially(self):
        manager, mock_pipeline = self._register_mock_manager()
        order: list[str] = []
        lock = threading.Lock()

        def set_layout(layout, *, graphics_state=None):
            with lock:
                order.append(f'start-{layout}')
            time.sleep(0.05)
            manager.layout = layout
            with lock:
                order.append(f'end-{layout}')

        mock_pipeline.set_layout.side_effect = set_layout

        first_started = threading.Event()

        def send_layout(layout: str, *, gate: threading.Event | None = None) -> None:
            if gate is not None:
                gate.wait(timeout=2)
            self.manager.send_command(
                ChangeLayoutCommand(
                    session_id=self.session_id,
                    layout=layout,
                )
            )
            if layout == 'A':
                first_started.set()

        first = threading.Thread(target=send_layout, args=('A',))
        second = threading.Thread(target=send_layout, kwargs={'gate': first_started, 'layout': 'B'})
        first.start()
        second.start()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertEqual(order, ['start-A', 'end-A', 'start-B', 'end-B'])

    def test_commands_run_on_glib_executor_thread(self):
        self._register_mock_manager()
        executor = get_executor_registry().get(self.session_id)
        assert executor is not None

        observed: list[int] = []

        def capture_thread() -> str:
            observed.append(threading.get_ident())
            return 'ok'

        result = executor.submit_callable('glib-thread', capture_thread)

        self.assertEqual(result, 'ok')
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0], executor.thread_id)

    def test_factory_defaults_to_inprocess_runtime(self):
        runtime = create_session_worker_runtime()
        self.assertIsInstance(runtime, InProcessSessionWorkerRuntime)
