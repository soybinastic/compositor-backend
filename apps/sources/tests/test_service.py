"""Tests for RTMP source ingest."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.sessions.models import StudioSession
from apps.sources.exceptions import InvalidRtmpUrlError
from apps.sources.service import RtmpSourceService


class RtmpSourceServiceTests(TestCase):
    def setUp(self):
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )
        self.service = RtmpSourceService()

    def test_validate_url_rejects_non_rtmp(self):
        with self.assertRaises(InvalidRtmpUrlError):
            self.service._validate_url('https://example.com/live.m3u8')

    @patch('apps.sources.service.get_ingest_manager')
    def test_add_source_persists_record(self, mock_get_manager):
        manager = MagicMock()
        mock_get_manager.return_value = manager

        result = self.service.add_source(
            self.session.id,
            url='rtmp://live.example.com/app/key',
            display_name='Output monitor',
        )

        self.assertTrue(result.source_id.startswith('rtmp-'))
        manager.add_rtmp_source.assert_called_once()
        self.assertEqual(result.url, 'rtmp://live.example.com/app/key')
        self.assertEqual(result.display_name, 'Output monitor')
