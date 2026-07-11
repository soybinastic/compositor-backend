from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.compositor.metrics import collect_metrics
from apps.compositor.registry import clear_all


class HealthMetricsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        clear_all()

    @patch('apps.sessions.views.check_mediasoup')
    @patch('apps.sessions.views.check_gstreamer')
    def test_health_includes_dependencies_and_metrics(self, mock_gst, mock_ms):
        mock_gst.return_value = {'available': True, 'version': '1.0', 'error': None}
        mock_ms.return_value = {'available': True, 'error': None}

        response = self.client.get('/api/v1/health/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ok')
        self.assertIn('metrics', response.data)
        self.assertIn('mediasoup', response.data)

    def test_metrics_endpoint(self):
        response = self.client.get('/api/v1/metrics/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, collect_metrics())
