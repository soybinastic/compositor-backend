"""Integration tests for the compositor background music branch."""

import shutil
import urllib.request
from unittest import skipUnless

from django.test import TestCase

from apps.compositor.compositor_pipeline import CompositorPipeline
from apps.scenes.constants import DEFAULT_BACKGROUND_MUSIC_CONFIG
from apps.sessions.models import LayoutType

HAS_GSTREAMER = shutil.which('gst-launch-1.0') is not None
TEST_TRACK_URL = 'https://studio-assets.b-cdn.net/bgm/twilight_drift.mp3'


def _can_fetch_test_track() -> bool:
    try:
        with urllib.request.urlopen(TEST_TRACK_URL, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


@skipUnless(HAS_GSTREAMER, 'GStreamer not available')
@skipUnless(_can_fetch_test_track(), 'Background music test track unavailable')
class BackgroundMusicPipelineTests(TestCase):
    def test_load_play_pause_stop_background_music(self):
        pipeline = CompositorPipeline(
            'bgm-test-session',
            width=640,
            height=360,
            fps=30,
            layout=LayoutType.CONTAIN,
            video_backend='cpu',
        )
        pipeline.start()

        config = {
            **DEFAULT_BACKGROUND_MUSIC_CONFIG,
            'track': {
                'asset_id': 'test-track',
                'url': TEST_TRACK_URL,
                'title': 'Twilight Drift',
            },
            'volume': 0.25,
            'muted': False,
            'loop': False,
        }
        pipeline.apply_background_music(config, scene_id='scene-1')

        ready_state = pipeline.get_background_music_state()
        self.assertEqual(ready_state['scene_id'], 'scene-1')
        self.assertEqual(ready_state['playback_state'], 'ready')

        playing_state = pipeline.play_background_music()
        self.assertEqual(playing_state['playback_state'], 'playing')

        paused_state = pipeline.pause_background_music()
        self.assertEqual(paused_state['playback_state'], 'paused')

        resumed_state = pipeline.resume_background_music()
        self.assertEqual(resumed_state['playback_state'], 'playing')

        stopped_state = pipeline.stop_background_music()
        self.assertEqual(stopped_state['playback_state'], 'stopped')

        pipeline.set_background_music_volume(0.1, muted=True)
        muted_state = pipeline.get_background_music_state()
        self.assertEqual(muted_state['playback_state'], 'stopped')

        pipeline.stop()
