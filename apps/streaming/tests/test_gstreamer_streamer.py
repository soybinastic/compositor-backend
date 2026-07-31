from django.test import SimpleTestCase

from apps.streaming.gstreamer_streamer import rtmp_sink_factory_names


class RtmpSinkFactorySelectionTests(SimpleTestCase):
    def test_plain_rtmp_prefers_rtmpsink(self):
        self.assertEqual(
            rtmp_sink_factory_names('rtmp://live.twitch.tv/app/key'),
            ('rtmpsink', 'rtmp2sink'),
        )

    def test_rtmps_prefers_rtmp2sink(self):
        self.assertEqual(
            rtmp_sink_factory_names(
                'rtmps://live-api-s.facebook.com:443/rtmp/FB-stream-key'
            ),
            ('rtmp2sink', 'rtmpsink'),
        )

    def test_rtmps_detection_is_case_insensitive(self):
        self.assertEqual(
            rtmp_sink_factory_names('RTMPS://example.com/live/key'),
            ('rtmp2sink', 'rtmpsink'),
        )
