"""Unit tests for tile config merge/sanitize helpers."""

from django.test import TestCase

from apps.compositor.tile_order import (
    merge_sources_config,
    merge_tile_order_config,
    sanitize_assignments_for_storage,
    sanitize_hidden_source_ids,
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
