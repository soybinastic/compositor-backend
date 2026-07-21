import shutil
from unittest import skipUnless

from django.test import TestCase

from apps.compositor.compositor_pipeline import CompositorPipeline
from apps.sessions.models import LayoutType

HAS_GSTREAMER = shutil.which('gst-launch-1.0') is not None


@skipUnless(HAS_GSTREAMER, 'GStreamer not available')
class CompositorPipelineTests(TestCase):
    def test_starts_and_applies_layout(self):
        pipeline = CompositorPipeline(
            'test-session',
            width=640,
            height=360,
            fps=30,
            layout=LayoutType.CONTAIN,
            video_backend='cpu',
        )
        pipeline.start()

        status = pipeline.get_status()
        self.assertEqual(status.layout, LayoutType.CONTAIN)
        self.assertEqual(status.canvas_width, 640)
        self.assertEqual(status.video_backend, 'cpu')
        self.assertEqual(status.requested_video_backend, 'cpu')

        pipeline.set_layout(LayoutType.THUMBNAIL)
        self.assertEqual(pipeline.get_status().layout, LayoutType.THUMBNAIL)

        pipeline.stop()
