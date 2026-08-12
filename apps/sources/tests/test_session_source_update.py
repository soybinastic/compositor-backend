"""Tests for SessionSourceService update / settings clear."""

from django.test import TestCase

from apps.sessions.models import SessionStatus, StudioSession
from apps.sources.models import SessionSource, SourceState, SourceType
from apps.sources.source_service import SourceService


class SessionSourceUpdateTests(TestCase):
    def setUp(self):
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token-update',
            status=SessionStatus.ACTIVE,
            mediasoup_compositor_peer_id='compositor-test',
        )
        self.service = SourceService()
        self.row = SessionSource.objects.create(
            session=self.session,
            source_id='screen-abc123',
            type=SourceType.SCREEN,
            name='Screen Share',
            state=SourceState.ACTIVE,
            settings={
                'peerId': 'host-1',
                'producerId': 'prod-video',
                'audioProducerId': 'prod-audio',
                'withSystemAudio': True,
            },
        )

    def test_update_clears_null_settings_keys_and_state(self):
        result = self.service.update_source(
            self.session.id,
            'screen-abc123',
            settings={
                'producerId': None,
                'audioProducerId': None,
                'withSystemAudio': True,
            },
            state=SourceState.STOPPED,
        )

        self.assertEqual(result.state, SourceState.STOPPED)
        self.assertNotIn('producerId', result.settings)
        self.assertNotIn('audioProducerId', result.settings)
        self.assertEqual(result.settings.get('peerId'), 'host-1')
        self.assertTrue(result.settings.get('withSystemAudio'))

        self.row.refresh_from_db()
        self.assertEqual(self.row.state, SourceState.STOPPED)
        self.assertNotIn('producerId', self.row.settings)
        self.assertNotIn('audioProducerId', self.row.settings)
