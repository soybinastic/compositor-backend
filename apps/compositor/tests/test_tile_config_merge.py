"""Unit tests for tile config merge/sanitize helpers."""

from django.test import TestCase

from apps.compositor.tile_order import (
    merge_sources_config,
    merge_tile_order_config,
    sanitize_assignments_for_storage,
    sanitize_hidden_source_ids,
    sync_items_z_index_from_assignments,
)
from apps.scenes.constants import DEFAULT_SOURCES_CONFIG
from apps.sessions.constants import DEFAULT_TILE_ORDER_CONFIG


class SanitizeHelpersTests(TestCase):
    def test_sanitize_assignments_normalizes_keys(self):
        self.assertEqual(
            sanitize_assignments_for_storage({0: 'host', '2': 'guest', '-1': 'bad'}),
            {'0': 'host', '2': 'guest'},
        )

    def test_sanitize_hidden_source_ids_dedupes(self):
        self.assertEqual(
            sanitize_hidden_source_ids(['a', ' a ', 'b', '', 'a']),
            ['a', 'b'],
        )


class MergeConfigTests(TestCase):
    def test_merge_tile_order_config_preserves_existing_assignments(self):
        existing = {**DEFAULT_TILE_ORDER_CONFIG, 'assignments': {'0': 'host'}}
        merged = merge_tile_order_config({'assignments': {'1': 'guest'}}, existing=existing)
        self.assertEqual(merged['assignments'], {'0': 'host', '1': 'guest'})

    def test_merge_sources_config_preserves_sources_list(self):
        existing = {**DEFAULT_SOURCES_CONFIG, 'sources': [{'id': 'cam-1'}]}
        merged = merge_sources_config({'assignments': {'0': 'host'}}, existing=existing)
        self.assertEqual(merged['sources'], [{'id': 'cam-1'}])
        self.assertEqual(merged['assignments'], {'0': 'host'})

    def test_merge_sources_config_replaces_assignments_when_items_rewritten(self):
        existing = {
            **DEFAULT_SOURCES_CONFIG,
            'items': [
                {'id': 'i1', 'sourceId': 'screen-a', 'visible': True, 'zIndex': 0},
                {'id': 'i2', 'sourceId': 'camera-b', 'visible': True, 'zIndex': 1},
            ],
            'assignments': {'0': 'screen-a', '1': 'camera-b'},
        }
        next_items = [
            {'id': 'i1', 'sourceId': 'screen-a', 'visible': True, 'zIndex': 0},
        ]
        merged = merge_sources_config(
            {
                'version': 2,
                'items': next_items,
                'assignments': {'0': 'screen-a'},
            },
            existing=existing,
        )
        self.assertEqual(merged['items'], next_items)
        self.assertEqual(merged['assignments'], {'0': 'screen-a'})
        self.assertNotIn('1', merged['assignments'])

    def test_merge_sources_config_assignments_only_syncs_item_z_index(self):
        existing = {
            **DEFAULT_SOURCES_CONFIG,
            'version': 2,
            'items': [
                {'id': 'i1', 'sourceId': 'screen-a', 'visible': True, 'zIndex': 0},
                {'id': 'i2', 'sourceId': 'camera-b', 'visible': True, 'zIndex': 1},
            ],
            'assignments': {'0': 'screen-a', '1': 'camera-b'},
        }
        merged = merge_sources_config(
            {'assignments': {'0': 'host-peer', '1': 'camera-b', '2': 'screen-a'}},
            existing=existing,
        )
        self.assertEqual(merged['assignments']['0'], 'host-peer')
        self.assertEqual(merged['assignments']['1'], 'camera-b')
        self.assertEqual(merged['assignments']['2'], 'screen-a')
        by_source = {item['sourceId']: item['zIndex'] for item in merged['items']}
        self.assertEqual(by_source['camera-b'], 0)
        self.assertEqual(by_source['screen-a'], 1)

    def test_sync_items_z_index_from_assignments_ignores_peer_slots(self):
        items = [
            {'id': 'i1', 'sourceId': 'screen-a', 'visible': True, 'zIndex': 0},
            {'id': 'i2', 'sourceId': 'camera-b', 'visible': True, 'zIndex': 1},
        ]
        ordered = sync_items_z_index_from_assignments(
            items,
            {'0': 'host-peer', '1': 'camera-b', '2': 'screen-a'},
        )
        self.assertEqual(
            [(item['sourceId'], item['zIndex']) for item in ordered],
            [('camera-b', 0), ('screen-a', 1)],
        )
