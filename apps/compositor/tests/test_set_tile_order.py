"""Tests for compositor pipeline tile ordering integration."""

import threading
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.compositor.compositor_pipeline import CompositorPipeline, ParticipantBranch
from apps.compositor.ingest_branch import IngestStats


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
        pipeline._uri_source_ids = set()
        pipeline._layout_manager = MagicMock()
        pipeline._compositor = MagicMock()
        pipeline._graphics = MagicMock()
        pipeline._graphics.background_active = False
        return pipeline

    def test_set_tile_order_applies_layout(self):
        pipeline = self._pipeline_stub()

        with patch.object(pipeline, '_apply_layout_unlocked') as apply_layout:
            with patch.object(pipeline, '_sync_uri_scene_audio_mute_unlocked') as sync_audio:
                pipeline.set_tile_order(
                    host_peer_id='host',
                    slot_assignments={'0': 'host'},
                    hidden_source_ids=['guest'],
                )

        self.assertEqual(pipeline._host_peer_id, 'host')
        self.assertEqual(pipeline._slot_assignments, {0: 'host'})
        self.assertEqual(pipeline._hidden_source_ids, frozenset({'guest'}))
        apply_layout.assert_called_once()
        sync_audio.assert_called_once()

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


class UriSceneAudioMuteTests(SimpleTestCase):
    def _uri_pipeline(
        self, *, source_id: str = 'prerecorded-1'
    ) -> tuple[CompositorPipeline, MagicMock]:
        pipeline = CompositorPipeline.__new__(CompositorPipeline)
        pipeline._lock = threading.Lock()
        pipeline._hidden_source_ids = frozenset()
        pipeline._uri_source_ids = {source_id}
        volume = MagicMock()
        branch = ParticipantBranch(
            participant_peer_id=source_id,
            compositor_sink_pad=MagicMock(),
            mixer_sink_pad=MagicMock(),
            stats=IngestStats(),
            audio_volume=volume,
            user_muted=False,
        )
        pipeline._participants = {source_id: branch}
        return pipeline, volume

    def test_hidden_uri_source_is_muted(self):
        pipeline, volume = self._uri_pipeline()
        pipeline._hidden_source_ids = frozenset({'prerecorded-1'})

        pipeline._sync_uri_scene_audio_mute_unlocked()

        volume.set_property.assert_called_with('mute', True)

    def test_visible_uri_source_is_unmuted(self):
        pipeline, volume = self._uri_pipeline()
        pipeline._hidden_source_ids = frozenset()

        pipeline._sync_uri_scene_audio_mute_unlocked()

        volume.set_property.assert_called_with('mute', False)

    def test_user_muted_stays_muted_when_visible(self):
        pipeline, volume = self._uri_pipeline()
        pipeline._participants['prerecorded-1'].user_muted = True

        pipeline._sync_uri_scene_audio_mute_unlocked()

        volume.set_property.assert_called_with('mute', True)

    def test_unhide_restores_user_unmuted(self):
        pipeline, volume = self._uri_pipeline()
        pipeline._hidden_source_ids = frozenset({'prerecorded-1'})
        pipeline._sync_uri_scene_audio_mute_unlocked()
        volume.reset_mock()

        pipeline._hidden_source_ids = frozenset()
        pipeline._sync_uri_scene_audio_mute_unlocked()

        volume.set_property.assert_called_with('mute', False)

    def test_set_tile_order_mutes_hidden_prerecorded(self):
        pipeline, volume = self._uri_pipeline()
        pipeline._layout = 'CONTAIN'
        pipeline._host_peer_id = 'host'
        pipeline._slot_assignments = None
        pipeline._host_owned_source_ids = {'prerecorded-1'}
        pipeline._layout_manager = MagicMock()
        pipeline._compositor = MagicMock()
        pipeline._graphics = MagicMock()
        pipeline._graphics.background_active = False
        pipeline._participants['host'] = MagicMock(compositor_sink_pad=MagicMock())

        with patch.object(pipeline, '_apply_layout_unlocked'):
            pipeline.set_tile_order(
                host_peer_id='host',
                hidden_source_ids=['prerecorded-1'],
            )

        volume.set_property.assert_called_with('mute', True)

    def test_playback_mute_cached_and_scene_hide_wins(self):
        pipeline, volume = self._uri_pipeline()
        pipeline._pipeline = None
        pipeline._hidden_source_ids = frozenset({'prerecorded-1'})

        pipeline.update_uri_video_playback(
            'prerecorded-1',
            action='volume',
            muted=False,
        )

        self.assertFalse(pipeline._participants['prerecorded-1'].user_muted)
        # Still scene-hidden → stay muted on the mix.
        volume.set_property.assert_any_call('mute', True)
