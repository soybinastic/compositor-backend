from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.compositor.metrics import collect_metrics
from apps.compositor.registry import clear_all, register
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.shutdown import graceful_shutdown
from core import events
from core.webhooks import emit_event


class WebhookTests(TestCase):
    @override_settings(WEBHOOK_URL='')
    def test_emit_event_noop_without_url(self):
        emit_event(events.SESSION_CREATED, {'session_id': 'test'})

    @override_settings(WEBHOOK_URL='http://example.com/hook', WEBHOOK_SECRET='secret')
    @patch('core.webhooks.urllib.request.urlopen')
    def test_emit_event_delivers_payload(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        emit_event(events.RECORDING_STARTED, {'session_id': 'abc'})
        import time

        time.sleep(0.2)


class MetricsTests(TestCase):
    def test_collect_metrics_empty(self):
        clear_all()
        metrics = collect_metrics()
        self.assertEqual(metrics['active_sessions'], 0)
        self.assertIn('producer_watcher_running', metrics)


class GracefulShutdownTests(TestCase):
    @patch('apps.compositor.producer_watcher.ProducerWatcher')
    @patch('apps.streaming.service.StreamingService')
    @patch('apps.recording.service.RecordingService')
    def test_graceful_shutdown_stops_managers(
        self,
        mock_recording_cls,
        mock_streaming_cls,
        mock_watcher_cls,
    ):
        mock_manager = MagicMock(spec=SessionIngestManager)
        mock_manager.session_id = '00000000-0000-0000-0000-000000000001'
        register(mock_manager)

        graceful_shutdown()

        mock_manager.stop.assert_called_once()
        mock_watcher_cls.instance.return_value.stop.assert_called_once()

        clear_all()
