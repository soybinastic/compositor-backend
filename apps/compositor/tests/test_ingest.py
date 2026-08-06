from unittest.mock import MagicMock

from django.test import TestCase, override_settings

from apps.compositor.consumer_service import ConsumerService
from apps.compositor.ports import PortAllocator
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.video_placeholder import placeholder_initials
from apps.sessions.models import StudioSession
from integrations.mediasoup.rtp import (
    build_audio_rtp_capabilities,
    get_codec_payload_type,
)


class VideoPlaceholderHelpersTests(TestCase):
    def test_placeholder_initials(self):
        self.assertEqual(placeholder_initials('Jane Doe'), 'JD')
        self.assertEqual(placeholder_initials('Host (You)'), 'H')
        self.assertEqual(placeholder_initials(''), '?')



class PortAllocatorTests(TestCase):
    def test_allocates_non_overlapping_ports(self):
        allocator = PortAllocator(min_port=50000, max_port=50020)
        ports_a = allocator.allocate_participant_ports('peer-a')
        ports_b = allocator.allocate_participant_ports('peer-b')

        used = {
            ports_a.audio.rtp_port,
            ports_a.audio.rtcp_port,
            ports_a.video.rtp_port,
            ports_a.video.rtcp_port,
            ports_b.audio.rtp_port,
            ports_b.audio.rtcp_port,
            ports_b.video.rtp_port,
            ports_b.video.rtcp_port,
        }
        self.assertEqual(len(used), 8)

    def test_release_and_reuse_ports(self):
        allocator = PortAllocator(min_port=50000, max_port=50010)
        ports = allocator.allocate_participant_ports('peer-a')
        rtp_port = ports.audio.rtp_port
        allocator.release_participant_ports(ports)
        ports_b = allocator.allocate_participant_ports('peer-b')
        self.assertEqual(ports_b.audio.rtp_port, rtp_port)


class RtpCapabilitiesTests(TestCase):
    def test_extract_payload_types(self):
        router_caps = {
            'codecs': [
                {
                    'kind': 'audio',
                    'mimeType': 'audio/opus',
                    'preferredPayloadType': 100,
                    'clockRate': 48000,
                    'channels': 2,
                },
                {
                    'kind': 'video',
                    'mimeType': 'video/VP8',
                    'preferredPayloadType': 101,
                    'clockRate': 90000,
                },
            ]
        }

        self.assertEqual(get_codec_payload_type(router_caps, 'audio/opus'), 100)
        self.assertEqual(get_codec_payload_type(router_caps, 'video/VP8'), 101)
        audio_caps = build_audio_rtp_capabilities(100)
        self.assertEqual(audio_caps['codecs'][0]['preferredPayloadType'], 100)

    def test_payload_type_from_consumer_rtp_parameters(self):
        from integrations.mediasoup.rtp import get_payload_type_from_rtp_parameters

        self.assertEqual(
            get_payload_type_from_rtp_parameters(
                {'codecs': [{'mimeType': 'audio/opus', 'payloadType': 111}]}
            ),
            111,
        )


def _av_peer(peer_id: str, audio_id: str, video_id: str, display_name: str = '') -> dict:
    return {
        'peerId': peer_id,
        'displayName': display_name or peer_id,
        'producers': [
            {'producerId': audio_id, 'kind': 'audio', 'source': 'audio'},
            {'producerId': video_id, 'kind': 'video', 'source': 'video'},
        ],
    }


class SessionIngestManagerTests(TestCase):
    def test_sync_producers_attaches_and_detaches(self):
        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token',
            mediasoup_compositor_peer_id='compositor-test',
        )

        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_consumer_service.joined = False
        mock_participant = MagicMock()
        mock_consumer_service.attach_participant.return_value = mock_participant
        mock_compositor_pipeline = MagicMock()

        manager = SessionIngestManager(
            session_id=str(session.id),
            room_id=str(session.id),
            compositor_peer_id='compositor-test',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )

        manager.sync_producers(
            [_av_peer('guest-1', 'audio-1', 'video-1', 'Guest')],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )

        mock_consumer_service.attach_participant.assert_called_once_with(
            'guest-1',
            'audio-1',
            'video-1',
        )

        manager.sync_producers([], joined_peers=[])
        mock_consumer_service.detach_participant.assert_called_once_with(mock_participant)

    def test_sync_producers_reattaches_when_producer_ids_change(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_consumer_service.joined = True
        first = MagicMock()
        first.participant_peer_id = 'guest-1'
        first.audio_producer_id = 'audio-1'
        first.video_producer_id = 'video-1'
        first.video_mode = 'rtp'
        second = MagicMock()
        second.participant_peer_id = 'guest-1'
        second.audio_producer_id = 'audio-2'
        second.video_producer_id = 'video-2'
        second.video_mode = 'rtp'
        mock_consumer_service.attach_participant.return_value = second
        mock_compositor_pipeline = MagicMock()

        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )
        manager._participants['guest-1'] = first

        manager.sync_producers(
            [_av_peer('guest-1', 'audio-2', 'video-2')],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )

        mock_consumer_service.detach_participant.assert_called_once_with(first)
        mock_consumer_service.attach_participant.assert_called_once_with(
            'guest-1',
            'audio-2',
            'video-2',
        )
        self.assertIs(manager._participants['guest-1'], second)

    @override_settings(VIDEO_SOFT_DISABLE_GRACE_SEC=0)
    def test_sync_soft_disables_when_webcam_missing_but_still_joined(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_consumer_service.joined = True
        current = MagicMock()
        current.participant_peer_id = 'guest-1'
        current.audio_producer_id = 'audio-1'
        current.video_producer_id = 'video-1'
        current.video_mode = 'rtp'
        mock_compositor_pipeline = MagicMock()

        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )
        manager._participants['guest-1'] = current

        audio_only = {
            'peerId': 'guest-1',
            'displayName': 'Guest',
            'producers': [
                {'producerId': 'audio-1', 'kind': 'audio', 'source': 'audio'},
            ],
        }

        # First poll starts grace (even with 0, missing_since is set then next check).
        manager.sync_producers(
            [audio_only],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )
        # Second poll past grace → soft disable.
        manager.sync_producers(
            [audio_only],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )

        mock_consumer_service.soft_disable_video.assert_called()
        mock_consumer_service.detach_participant.assert_not_called()
        self.assertIn('guest-1', manager._participants)

    def test_sync_hard_detaches_when_peer_leaves(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        current = MagicMock()
        current.video_mode = 'placeholder'
        mock_compositor_pipeline = MagicMock()
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )
        manager._participants['guest-1'] = current

        manager.sync_producers([], joined_peers=[])
        mock_consumer_service.detach_participant.assert_called_once_with(current)
        self.assertNotIn('guest-1', manager._participants)

    def test_set_layout_updates_compositor_pipeline(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_compositor_pipeline = MagicMock()
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )

        manager.set_layout('THUMBNAIL')

        self.assertEqual(manager.layout, 'THUMBNAIL')
        mock_compositor_pipeline.set_layout.assert_called_once_with(
            'THUMBNAIL',
            graphics_state=None,
        )

    def test_skips_compositor_peer(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        mock_compositor_pipeline = MagicMock()
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=mock_compositor_pipeline,
        )

        manager.sync_producers(
            [{'peerId': 'compositor-session-1', 'producers': []}],
            joined_peers=[{'peerId': 'compositor-session-1', 'displayName': 'Compositor'}],
        )

        mock_consumer_service.attach_participant.assert_not_called()
