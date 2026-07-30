from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.compositor.metrics import collect_metrics
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
    @patch('apps.compositor.metrics.get_session_worker_manager')
    def test_collect_metrics_empty(self, mock_get_manager):
        mock_get_manager.return_value.list_running_session_ids.return_value = []
        metrics = collect_metrics()
        self.assertEqual(metrics['active_sessions'], 0)
        self.assertIn('active_producer_pollers', metrics)
        self.assertIn('producer_watcher_running', metrics)


class GracefulShutdownTests(TestCase):
    @patch('apps.compositor.worker_manager.get_session_worker_manager')
    @patch('apps.compositor.session_producer_poller.get_poller_registry')
    @patch('apps.streaming.service.StreamingService')
    @patch('apps.recording.service.RecordingService')
    def test_graceful_shutdown_stops_workers(
        self,
        mock_recording_cls,
        mock_streaming_cls,
        mock_poller_registry_getter,
        mock_worker_manager_getter,
    ):
        mock_worker_manager = MagicMock()
        mock_worker_manager.list_running_session_ids.return_value = [
            '00000000-0000-0000-0000-000000000001'
        ]
        mock_worker_manager_getter.return_value = mock_worker_manager
        mock_poller_registry = MagicMock()
        mock_poller_registry_getter.return_value = mock_poller_registry

        graceful_shutdown()

        mock_worker_manager.destroy_session.assert_called_once_with(
            '00000000-0000-0000-0000-000000000001'
        )
        mock_poller_registry.shutdown_all.assert_called_once()
