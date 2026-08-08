from django.test import TestCase

from apps.compositor.tile_order import (
    GRID_MAX_VISIBLE,
    attached_source_ids_from_scene_items,
    default_source_order,
    hidden_session_sources_not_on_scene,
    hidden_source_ids_from_scene_items,
    layout_max_visible,
    merge_hidden_source_ids,
    normalize_slot_assignments,
    resolve_effective_assignments,
    resolve_source_order,
)
from apps.layouts.types import LayoutType
from apps.sessions.constants import DEFAULT_TILE_ORDER_CONFIG
from apps.sessions.models import StudioSession


class NormalizeSlotAssignmentsTests(TestCase):
    def test_parses_string_keys(self):
        self.assertEqual(
            normalize_slot_assignments({'0': 'host', '2': 'guest'}),
            {0: 'host', 2: 'guest'},
        )

    def test_skips_invalid_entries(self):
        self.assertEqual(
            normalize_slot_assignments({'0': 'host', '-1': 'bad', 'x': 'bad', '1': ''}),
            {0: 'host'},
        )


class DefaultSourceOrderTests(TestCase):
    def test_host_first_despite_join_order(self):
        ordered = default_source_order(
            ['guest', 'host'],
            host_peer_id='host',
        )
        self.assertEqual(ordered, ['host', 'guest'])

    def test_host_owned_sources_after_host(self):
        ordered = default_source_order(
            ['guest', 'host', 'rtmp-abc'],
            host_peer_id='host',
            host_owned_source_ids={'rtmp-abc'},
        )
        self.assertEqual(ordered, ['host', 'rtmp-abc', 'guest'])


class ResolveSourceOrderTests(TestCase):
    def test_guest_joined_before_host_defaults_host_to_slot_zero(self):
        ordered = resolve_source_order(
            ['guest', 'host'],
            host_peer_id='host',
        )
        self.assertEqual(ordered[0], 'host')

    def test_rtmp_defaults_after_host(self):
        ordered = resolve_source_order(
            ['guest', 'host', 'rtmp-1'],
            host_peer_id='host',
            host_owned_source_ids={'rtmp-1'},
        )
        self.assertEqual(ordered, ['host', 'rtmp-1', 'guest'])

    def test_explicit_guest_at_slot_zero(self):
        ordered = resolve_source_order(
            ['host', 'guest'],
            host_peer_id='host',
            slot_assignments={0: 'guest', 1: 'host'},
        )
        self.assertEqual(ordered, ['guest', 'host'])

    def test_partial_assignment_fills_with_defaults(self):
        ordered = resolve_source_order(
            ['host', 'guest', 'guest-2'],
            host_peer_id='host',
            slot_assignments={0: 'guest'},
        )
        self.assertEqual(ordered[0], 'guest')
        self.assertIn('host', ordered[1:])
        self.assertIn('guest-2', ordered[1:])

    def test_hidden_participant_excluded(self):
        ordered = resolve_source_order(
            ['host', 'guest'],
            host_peer_id='host',
            hidden_source_ids={'guest'},
        )
        self.assertEqual(ordered, ['host'])

    def test_grid_overflow_hides_extras(self):
        sources = [f'p{i}' for i in range(12)]
        ordered = resolve_source_order(
            sources,
            host_peer_id='p0',
            max_visible=GRID_MAX_VISIBLE,
        )
        self.assertEqual(len(ordered), GRID_MAX_VISIBLE)
        self.assertEqual(ordered[0], 'p0')

    def test_empty_assignments_use_default_order(self):
        ordered = resolve_source_order(
            ['guest', 'host'],
            host_peer_id='host',
            slot_assignments={},
        )
        self.assertEqual(ordered, ['host', 'guest'])

    def test_sparse_assignment_preserves_slot_index(self):
        ordered = resolve_source_order(
            ['guest-1', 'guest-2', 'host'],
            host_peer_id='host',
            slot_assignments={2: 'host'},
        )
        self.assertEqual(ordered[2], 'host')
        self.assertEqual(len(ordered), 3)

    def test_invalid_assignment_source_skipped(self):
        ordered = resolve_source_order(
            ['host', 'guest'],
            host_peer_id='host',
            slot_assignments={0: 'missing', 1: 'guest'},
        )
        self.assertEqual(ordered[0], 'host')
        self.assertEqual(ordered[1], 'guest')


class ResolveEffectiveAssignmentsTests(TestCase):
    def test_scene_override_beats_session(self):
        session = {'version': 1, 'assignments': {'0': 'host-a'}}
        scene = {'version': 1, 'sources': [], 'assignments': {'0': 'guest-b'}}
        self.assertEqual(
            resolve_effective_assignments(session, scene),
            {0: 'guest-b'},
        )

    def test_session_used_when_scene_assignments_empty(self):
        session = {'version': 1, 'assignments': {'1': 'host-a'}}
        scene = {'version': 1, 'sources': [], 'assignments': {}}
        self.assertEqual(
            resolve_effective_assignments(session, scene),
            {1: 'host-a'},
        )

    def test_none_when_both_empty(self):
        session = dict(DEFAULT_TILE_ORDER_CONFIG)
        scene = {'version': 1, 'sources': [], 'assignments': {}}
        self.assertIsNone(resolve_effective_assignments(session, scene))


class LayoutMaxVisibleTests(TestCase):
    def test_grid_capped_at_nine(self):
        self.assertEqual(layout_max_visible(LayoutType.GRID.value), GRID_MAX_VISIBLE)

    def test_contain_uncapped(self):
        self.assertIsNone(layout_max_visible(LayoutType.CONTAIN.value))


class SessionTileOrderModelTests(TestCase):
    def test_save_applies_default_tile_order_config(self):
        session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token='token-tile-order',
        )
        self.assertEqual(session.tile_order_config['version'], 1)
        self.assertEqual(session.tile_order_config['assignments'], {})
        self.assertEqual(session.hidden_source_ids, [])
        self.assertIsNone(session.host_peer_id)


class SceneItemHiddenIdsTests(TestCase):
    def test_hidden_from_invisible_items(self):
        items = [
            {'sourceId': 'camera-a', 'visible': True, 'zIndex': 0},
            {'sourceId': 'camera-b', 'visible': False, 'zIndex': 1},
            {'sourceId': 'camera-c', 'visible': False, 'zIndex': 2},
        ]
        self.assertEqual(
            hidden_source_ids_from_scene_items(items),
            ['camera-b', 'camera-c'],
        )

    def test_attached_includes_hidden_items(self):
        items = [
            {'sourceId': 'camera-a', 'visible': True, 'zIndex': 0},
            {'sourceId': 'camera-b', 'visible': False, 'zIndex': 1},
        ]
        self.assertEqual(
            attached_source_ids_from_scene_items(items),
            ['camera-a', 'camera-b'],
        )

    def test_not_on_scene_sources_are_hidden(self):
        items = [
            {'sourceId': 'camera-a', 'visible': True, 'zIndex': 0},
        ]
        self.assertEqual(
            hidden_session_sources_not_on_scene(
                ['camera-a', 'camera-b', 'prerecorded-1'],
                items,
            ),
            ['camera-b', 'prerecorded-1'],
        )

    def test_empty_scene_hides_all_session_sources(self):
        self.assertEqual(
            hidden_session_sources_not_on_scene(
                ['camera-a', 'camera-b'],
                [],
            ),
            ['camera-a', 'camera-b'],
        )

    def test_merge_session_and_scene_hidden(self):
        self.assertEqual(
            merge_hidden_source_ids(['guest-1'], ['camera-b', 'guest-1']),
            ['guest-1', 'camera-b'],
        )
