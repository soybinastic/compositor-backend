"""Tests for compositor pipeline tile ordering integration."""

import threading
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.compositor.compositor_pipeline import CompositorPipeline


class SetTileOrderPipelineTests(SimpleTestCase):
    def _pipeline_stub(self) -> CompositorPipeline:
        pipeline = CompositorPipeline.__new__(CompositorPipeline)
        pipeline._lock = threading.Lock()
        pipeline._layout = 'CONTAIN'
        pipeline._participants = {'guest': MagicMock(), 'host': MagicMock()}
        pipeline._host_peer_id = None
        pipeline._slot_assignments = None
        pipeline._hidden_source_ids = frozenset()
        pipeline._host_owned_source_ids = set()
        pipeline._layout_manager = MagicMock()
        pipeline._compositor = MagicMock()
        pipeline._graphics = MagicMock()
        pipeline._graphics.background_active = False
        return pipeline

    def test_set_tile_order_applies_layout(self):
        pipeline = self._pipeline_stub()

        with patch.object(pipeline, '_apply_layout_unlocked') as apply_layout:
            pipeline.set_tile_order(
                host_peer_id='host',
                slot_assignments={'0': 'host'},
                hidden_source_ids=['guest'],
            )

        self.assertEqual(pipeline._host_peer_id, 'host')
        self.assertEqual(pipeline._slot_assignments, {0: 'host'})
        self.assertEqual(pipeline._hidden_source_ids, frozenset({'guest'}))
        apply_layout.assert_called_once()

    def test_ordered_source_ids_puts_host_first_by_default(self):
        pipeline = self._pipeline_stub()
        pipeline._host_peer_id = 'host'

        ordered = pipeline._ordered_source_ids_unlocked()
        self.assertEqual(ordered[0], 'host')

    def test_ordered_source_ids_respects_hidden_sources(self):
        pipeline = self._pipeline_stub()
        pipeline._host_peer_id = 'host'
        pipeline._hidden_source_ids = frozenset({'guest'})

        ordered = pipeline._ordered_source_ids_unlocked()
        self.assertEqual(ordered, ['host'])

    def test_ordered_source_ids_respects_explicit_assignments(self):
        pipeline = self._pipeline_stub()
        pipeline._host_peer_id = 'host'
        pipeline._slot_assignments = {0: 'guest', 1: 'host'}

        ordered = pipeline._ordered_source_ids_unlocked()
        self.assertEqual(ordered, ['guest', 'host'])

    def test_apply_layout_uses_ordered_sources(self):
        pipeline = self._pipeline_stub()
        pipeline._host_peer_id = 'host'

        with patch.object(pipeline, '_ordered_source_ids_unlocked', return_value=['host', 'guest']):
            with patch.object(pipeline, '_hide_pad'):
                with patch.object(pipeline, '_apply_tile_to_pad'):
                    pipeline._apply_layout_unlocked()

        pipeline._layout_manager.compute_tiles.assert_called_once_with(
            ['host', 'guest'],
            host_source_id='host',
        )
