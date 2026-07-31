"""Tests for set_layout graphics application."""

import threading
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.compositor.compositor_pipeline import CompositorPipeline


class SetLayoutGraphicsTests(SimpleTestCase):
    def _pipeline_stub(self) -> CompositorPipeline:
        pipeline = CompositorPipeline.__new__(CompositorPipeline)
        pipeline._lock = threading.Lock()
        pipeline._layout = 'CONTAIN'
        pipeline._layout_manager = MagicMock()
        pipeline._graphics = MagicMock()
        pipeline._graphics._pending_state = {}
        pipeline._graphics.prefetch_background_still.return_value = None
        return pipeline

    def test_set_layout_with_graphics_state_applies_live_layers(self):
        pipeline = self._pipeline_stub()
        graphics_state = {'logo': None, 'banner': None, 'background': None}

        with patch.object(pipeline, '_apply_layout_unlocked') as apply_layout:
            pipeline.set_layout('GRID', graphics_state=graphics_state)

        pipeline._graphics.apply_state.assert_called_once_with(
            graphics_state,
            layout='GRID',
            layout_only=False,
            prepared_background=None,
        )
        pipeline._graphics.set_pending_state.assert_not_called()
        apply_layout.assert_called_once_with(prepared_background=None)
        self.assertEqual(pipeline._layout, 'GRID')

    def test_set_layout_without_graphics_state_skips_apply_state(self):
        pipeline = self._pipeline_stub()

        with patch.object(pipeline, '_apply_layout_unlocked'):
            pipeline.set_layout('SPOTLIGHT')

        pipeline._graphics.apply_state.assert_not_called()
        pipeline._graphics.set_pending_state.assert_not_called()
