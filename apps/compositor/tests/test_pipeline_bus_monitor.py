import threading
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from apps.compositor.pipeline_bus_monitor import PipelineBusMonitor


class PipelineBusMonitorStopTests(SimpleTestCase):
    def test_stop_from_monitor_thread_does_not_join_self(self):
        monitor = PipelineBusMonitor(
            MagicMock(),
            watched_elements=set(),
            on_error=lambda _message: None,
        )
        monitor._thread = threading.current_thread()
        monitor.stop()

    def test_stop_from_other_thread_joins_monitor_thread(self):
        monitor = PipelineBusMonitor(
            MagicMock(),
            watched_elements=set(),
            on_error=lambda _message: None,
        )
        started = threading.Event()
        release = threading.Event()

        def run_monitor():
            started.set()
            release.wait(timeout=2)

        monitor._thread = threading.Thread(target=run_monitor, daemon=True)
        monitor._thread.start()
        self.assertTrue(started.wait(timeout=2))

        release.set()
        monitor.stop()
        self.assertFalse(monitor._thread.is_alive())
