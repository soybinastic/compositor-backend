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

    def test_render_participant_placeholder_image_size_and_opaque(self):
        from apps.compositor.video_placeholder import render_participant_placeholder_image

        image = render_participant_placeholder_image(
            display_name='Jane Doe',
            width=320,
            height=180,
        )
        self.assertEqual(image.size, (320, 180))
        self.assertEqual(image.mode, 'RGBA')
        # Sample near the left edge of the avatar circle (away from initials).
        short = min(320, 180)
        diameter = max(48, int(short * 0.22))
        cx, cy = 160, int(180 * 0.44)
        edge_x = cx - diameter // 2 + 4
        self.assertEqual(image.getpixel((edge_x, cy))[:3], (63, 63, 70))
        # Corner stays zinc-800 background.
        self.assertEqual(image.getpixel((2, 2))[:3], (39, 39, 42))
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
            owner_peer_id='guest-1',
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
            owner_peer_id='guest-1',
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

        manager.sync_producers(
            [audio_only],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )

        mock_consumer_service.soft_disable_video.assert_called_once()
        mock_consumer_service.detach_participant.assert_not_called()
        self.assertIn('guest-1', manager._participants)

    @override_settings(VIDEO_SOFT_DISABLE_GRACE_SEC=0)
    def test_sync_soft_enables_video_when_webcam_returns_without_mic(self):
        """Mic-muted guests must leave placeholder when webcam alone returns."""
        mock_consumer_service = MagicMock(spec=ConsumerService)
        current = MagicMock()
        current.participant_peer_id = 'guest-1'
        current.audio_producer_id = 'audio-1'
        current.video_producer_id = None
        current.video_mode = 'placeholder'
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=MagicMock(),
        )
        manager._participants['guest-1'] = current

        video_only = {
            'peerId': 'guest-1',
            'displayName': 'Guest',
            'producers': [
                {'producerId': 'video-2', 'kind': 'video', 'source': 'video'},
            ],
        }

        manager.sync_producers(
            [video_only],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )

        mock_consumer_service.soft_enable_video.assert_called_once_with(
            current,
            'video-2',
            display_name='Guest',
        )
        mock_consumer_service.attach_participant.assert_not_called()
        mock_consumer_service.detach_participant.assert_not_called()

    def test_sync_soft_enables_when_scene_camera_changes_while_mic_muted(self):
        """Scene switch: new video id while still rtp + no mic must not stay frozen."""
        mock_consumer_service = MagicMock(spec=ConsumerService)
        current = MagicMock()
        current.participant_peer_id = 'host-1'
        current.audio_producer_id = 'audio-old'
        current.video_producer_id = 'video-old'
        current.video_mode = 'rtp'
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=MagicMock(),
        )
        manager._participants['host-1'] = current

        video_only = {
            'peerId': 'host-1',
            'displayName': 'Host',
            'producers': [
                {'producerId': 'video-new', 'kind': 'video', 'source': 'video'},
            ],
        }

        manager.sync_producers(
            [video_only],
            joined_peers=[{'peerId': 'host-1', 'displayName': 'Host'}],
        )

        mock_consumer_service.soft_enable_video.assert_called_once_with(
            current,
            'video-new',
            display_name='Host',
        )
        mock_consumer_service.detach_participant.assert_not_called()
        mock_consumer_service.attach_participant.assert_not_called()

    def test_sync_reattaches_when_scene_camera_changes_with_mic(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        current = MagicMock()
        current.participant_peer_id = 'host-1'
        current.audio_producer_id = 'audio-1'
        current.video_producer_id = 'video-old'
        current.video_mode = 'rtp'
        replacement = MagicMock()
        mock_consumer_service.attach_participant.return_value = replacement
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=MagicMock(),
        )
        manager._participants['host-1'] = current

        manager.sync_producers(
            [
                {
                    'peerId': 'host-1',
                    'displayName': 'Host',
                    'producers': [
                        {'producerId': 'audio-1', 'kind': 'audio', 'source': 'audio'},
                        {'producerId': 'video-new', 'kind': 'video', 'source': 'video'},
                    ],
                }
            ],
            joined_peers=[{'peerId': 'host-1', 'displayName': 'Host'}],
        )

        mock_consumer_service.detach_participant.assert_called_once_with(current)
        mock_consumer_service.attach_participant.assert_called_once_with(
            'host-1',
            'audio-1',
            'video-new',
            owner_peer_id='host-1',
        )
        mock_consumer_service.soft_enable_video.assert_not_called()
        self.assertIs(manager._participants['host-1'], replacement)

    @override_settings(VIDEO_SOFT_DISABLE_GRACE_SEC=5)
    def test_sync_respects_soft_disable_grace_when_configured(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        current = MagicMock()
        current.participant_peer_id = 'guest-1'
        current.audio_producer_id = 'audio-1'
        current.video_producer_id = 'video-1'
        current.video_mode = 'rtp'
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=MagicMock(),
        )
        manager._participants['guest-1'] = current

        audio_only = {
            'peerId': 'guest-1',
            'displayName': 'Guest',
            'producers': [
                {'producerId': 'audio-1', 'kind': 'audio', 'source': 'audio'},
            ],
        }

        manager.sync_producers(
            [audio_only],
            joined_peers=[{'peerId': 'guest-1', 'displayName': 'Guest'}],
        )
        mock_consumer_service.soft_disable_video.assert_not_called()
        self.assertIn('guest-1', manager._video_missing_since)

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

    def test_sync_attaches_extra_seats_for_source_id_videos(self):
        mock_consumer_service = MagicMock(spec=ConsumerService)
        primary = MagicMock()
        primary.participant_peer_id = 'host-1'
        primary.audio_producer_id = 'audio-1'
        primary.video_producer_id = 'video-main'
        primary.video_mode = 'rtp'
        primary.owner_peer_id = 'host-1'
        primary.source_id = None
        extra = MagicMock()
        extra.participant_peer_id = 'camera-abc'
        extra.owner_peer_id = 'host-1'
        extra.source_id = 'camera-abc'
        extra.video_producer_id = 'video-cam'
        extra.video_mode = 'rtp'
        mock_consumer_service.attach_participant.return_value = primary
        mock_consumer_service.attach_video_seat.return_value = extra
        manager = SessionIngestManager(
            session_id='session-1',
            room_id='session-1',
            compositor_peer_id='compositor-session-1',
            layout='CONTAIN',
            consumer_service=mock_consumer_service,
            compositor_pipeline=MagicMock(),
        )

        manager.sync_producers(
            [
                {
                    'peerId': 'host-1',
                    'displayName': 'Host',
                    'producers': [
                        {'producerId': 'audio-1', 'kind': 'audio', 'source': 'audio'},
                        {'producerId': 'video-main', 'kind': 'video', 'source': 'video'},
                        {
                            'producerId': 'video-cam',
                            'kind': 'video',
                            'source': 'video',
                            'sourceId': 'camera-abc',
                        },
                        {
                            'producerId': 'video-screen',
                            'kind': 'video',
                            'source': 'screensharing',
                            'sourceId': 'screen-xyz',
                        },
                    ],
                }
            ],
            joined_peers=[{'peerId': 'host-1', 'displayName': 'Host'}],
        )

        mock_consumer_service.attach_participant.assert_called_once_with(
            'host-1',
            'audio-1',
            'video-main',
            owner_peer_id='host-1',
        )
        self.assertEqual(mock_consumer_service.attach_video_seat.call_count, 2)
        mock_consumer_service.attach_video_seat.assert_any_call(
            'camera-abc',
            'video-cam',
            owner_peer_id='host-1',
            source_id='camera-abc',
            display_name='Host',
            host_owned=True,
        )
        mock_consumer_service.attach_video_seat.assert_any_call(
            'screen-xyz',
            'video-screen',
            owner_peer_id='host-1',
            source_id='screen-xyz',
            display_name='Host',
            host_owned=True,
        )

    def test_extract_av_producers_keeps_all_videos_with_source_id(self):
        audio_id, videos = SessionIngestManager._extract_av_producers(
            {
                'peerId': 'host-1',
                'producers': [
                    {'producerId': 'a1', 'kind': 'audio', 'source': 'audio'},
                    {'producerId': 'v1', 'kind': 'video', 'source': 'video'},
                    {
                        'producerId': 'v2',
                        'kind': 'video',
                        'source': 'video',
                        'sourceId': 'camera-1',
                    },
                ],
            }
        )
        self.assertEqual(audio_id, 'a1')
        self.assertEqual(len(videos), 2)
        primary, extras = SessionIngestManager._split_primary_and_extra(videos)
        self.assertIsNotNone(primary)
        assert primary is not None
        self.assertEqual(primary.producer_id, 'v1')
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0].source_id, 'camera-1')
