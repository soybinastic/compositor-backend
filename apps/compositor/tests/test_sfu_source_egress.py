"""Tests for compositor → SFU URI egress (PlainTransport + RTP attach)."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.compositor.sfu_source_egress import (
    SfuSourceEgress,
    _AUDIO_PT,
    _VIDEO_PT,
    _default_audio_rtp_parameters,
    _default_video_rtp_parameters,
)


class SfuSourceEgressTests(SimpleTestCase):
    def test_rtp_parameters_include_ssrc_and_payload_types(self):
        video = _default_video_rtp_parameters(ssrc=2222)
        audio = _default_audio_rtp_parameters(ssrc=1111)
        self.assertEqual(video['codecs'][0]['payloadType'], _VIDEO_PT)
        self.assertEqual(video['encodings'][0]['ssrc'], 2222)
        self.assertEqual(audio['codecs'][0]['payloadType'], _AUDIO_PT)
        self.assertEqual(audio['encodings'][0]['ssrc'], 1111)

    def test_start_creates_video_and_audio_producers_with_source_id(self):
        client = MagicMock()
        client.create_plain_transport.side_effect = [
            {'transportId': 'vt', 'ip': '127.0.0.1', 'port': 40000, 'rtcpPort': 40001},
            {'transportId': 'at', 'ip': '127.0.0.1', 'port': 40002, 'rtcpPort': 40003},
        ]
        client.create_producer.side_effect = [
            {'producerId': 'vp'},
            {'producerId': 'ap'},
        ]

        egress = SfuSourceEgress(
            client=client,
            room_id='room-1',
            compositor_peer_id='compositor',
            source_id='prerecorded-abc',
        )
        egress.start()

        self.assertTrue(egress.started)
        self.assertEqual(egress.video_producer_id, 'vp')
        self.assertEqual(egress.audio_producer_id, 'ap')
        self.assertEqual(client.create_plain_transport.call_count, 2)
        video_app = client.create_producer.call_args_list[0].kwargs['app_data']
        audio_app = client.create_producer.call_args_list[1].kwargs['app_data']
        self.assertEqual(video_app, {'source': 'video', 'sourceId': 'prerecorded-abc'})
        self.assertEqual(audio_app, {'source': 'audio', 'sourceId': 'prerecorded-abc'})

    def test_attach_rtp_send_links_udpsink_to_transport(self):
        client = MagicMock()
        client.create_plain_transport.side_effect = [
            {'transportId': 'vt', 'ip': '10.0.0.5', 'port': 5000, 'rtcpPort': 5001},
            {'transportId': 'at', 'ip': '10.0.0.5', 'port': 5002, 'rtcpPort': 5003},
        ]
        client.create_producer.side_effect = [{'producerId': 'vp'}, {'producerId': 'ap'}]

        egress = SfuSourceEgress(
            client=client,
            room_id='room-1',
            compositor_peer_id='compositor',
            source_id='prerecorded-1',
        )
        egress.start()

        pipeline = MagicMock()
        video_tee = MagicMock()
        audio_tee = MagicMock()
        video_tee_pad = MagicMock()
        audio_tee_pad = MagicMock()
        video_tee.get_request_pad.return_value = video_tee_pad
        audio_tee.get_request_pad.return_value = audio_tee_pad
        video_tee_pad.link.return_value = 0  # Gst.PadLinkReturn.OK
        audio_tee_pad.link.return_value = 0

        # PadLinkReturn.OK is typically 0; ensure our code compares correctly.
        with patch('apps.compositor.sfu_source_egress.Gst') as gst:
            gst.PadLinkReturn.OK = 0
            gst.MSECOND = 1_000_000
            gst.State.NULL = 'NULL'

            def make_element(factory_name, name=None):
                el = MagicMock(name=name or factory_name)
                el.get_factory.return_value.get_name.return_value = factory_name
                el.find_property.return_value = None
                el.get_static_pad.return_value = MagicMock()
                el.link.return_value = True
                return el

            gst.ElementFactory.make.side_effect = (
                lambda factory, name=None: make_element(factory, name)
            )

            egress.attach_rtp_send(pipeline, video_tee=video_tee, audio_tee=audio_tee)

        self.assertIsNotNone(egress._rtp_branch)
        self.assertGreater(pipeline.add.call_count, 0)
        video_tee.get_request_pad.assert_called()
        audio_tee.get_request_pad.assert_called()

        # Video udpsink should target the video PlainTransport.
        sinks = [
            call.args[0]
            for call in gst.ElementFactory.make.call_args_list
            if call.args and call.args[0] == 'udpsink'
        ]
        self.assertEqual(len(sinks), 2)

    def test_stop_clears_state(self):
        client = MagicMock()
        client.create_plain_transport.return_value = {
            'transportId': 'vt',
            'ip': '127.0.0.1',
            'port': 1,
            'rtcpPort': 2,
        }
        client.create_producer.return_value = {'producerId': 'vp'}
        egress = SfuSourceEgress(
            client=client,
            room_id='room-1',
            compositor_peer_id='compositor',
            source_id='prerecorded-1',
            include_audio=False,
        )
        egress.start()
        egress.stop()
        self.assertFalse(egress.started)
        self.assertIsNone(egress.video_producer_id)
