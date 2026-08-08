"""Tests for tile order command building and worker sync."""

from django.test import TestCase

from apps.compositor.commands import SetTileOrderCommand
from apps.compositor.tile_order_sync import build_set_tile_order_command
from apps.scenes.models import SceneType, StudioScene
from apps.sessions.constants import DEFAULT_TILE_ORDER_CONFIG
from apps.sessions.models import LayoutType, SessionStatus, StudioSession
from apps.sessions.services.invite_service import InviteService
from apps.sources.models import SessionSource, SourceState, SourceType


class BuildSetTileOrderCommandTests(TestCase):
    def setUp(self):
        self.session = StudioSession.objects.create(
            host_display_name='Host',
            invite_token=InviteService().generate_token(),
            layout=LayoutType.GRID,
            status=SessionStatus.ACTIVE,
            host_peer_id='host-peer',
            tile_order_config={
                **DEFAULT_TILE_ORDER_CONFIG,
                'assignments': {'0': 'host-peer'},
            },
            hidden_source_ids=['guest-hidden'],
        )
        self.scene = StudioScene.objects.create(
            session=self.session,
            name='Scene 1',
            scene_type=SceneType.CAMERA,
            sort_order=0,
            layout=LayoutType.GRID,
            sources_config={
                'version': 1,
                'sources': [],
                'assignments': {'0': 'guest-peer'},
            },
        )
        self.session.active_scene = self.scene
        self.session.save(update_fields=['active_scene_id'])

    def test_uses_session_defaults_when_no_scene_override(self):
        self.scene.sources_config = {'version': 1, 'sources': [], 'assignments': {}}
        self.scene.save(update_fields=['sources_config'])

        command = build_set_tile_order_command(self.session, scene=self.scene)
        self.assertIsInstance(command, SetTileOrderCommand)
        self.assertEqual(command.host_peer_id, 'host-peer')
        self.assertEqual(command.slot_assignments, {'0': 'host-peer'})
        self.assertEqual(command.hidden_source_ids, ['guest-hidden'])

    def test_scene_override_beats_session(self):
        command = build_set_tile_order_command(self.session, scene=self.scene)
        self.assertEqual(command.slot_assignments, {'0': 'guest-peer'})

    def test_no_assignments_when_both_empty(self):
        self.session.tile_order_config = dict(DEFAULT_TILE_ORDER_CONFIG)
        self.session.save(update_fields=['tile_order_config'])
        self.scene.sources_config = {'version': 1, 'sources': [], 'assignments': {}}
        self.scene.save(update_fields=['sources_config'])

        command = build_set_tile_order_command(self.session, scene=self.scene)
        self.assertIsNone(command.slot_assignments)

    def test_hides_session_sources_not_on_active_scene(self):
        SessionSource.objects.create(
            session=self.session,
            source_id='camera-a',
            type=SourceType.CAMERA,
            name='Cam A',
            state=SourceState.ACTIVE,
        )
        SessionSource.objects.create(
            session=self.session,
            source_id='camera-b',
            type=SourceType.CAMERA,
            name='Cam B',
            state=SourceState.ACTIVE,
        )
        self.scene.sources_config = {
            'version': 2,
            'items': [
                {
                    'id': 'item-a',
                    'sceneId': str(self.scene.id),
                    'sourceId': 'camera-a',
                    'visible': True,
                    'zIndex': 0,
                },
            ],
            'assignments': {'0': 'camera-a'},
        }
        self.scene.save(update_fields=['sources_config'])

        command = build_set_tile_order_command(self.session, scene=self.scene)
        self.assertEqual(command.slot_assignments, {'0': 'camera-a'})
        self.assertEqual(
            command.hidden_source_ids,
            ['guest-hidden', 'camera-b'],
        )

    def test_hides_invisible_attached_and_unattached_sources(self):
        SessionSource.objects.create(
            session=self.session,
            source_id='camera-a',
            type=SourceType.CAMERA,
            name='Cam A',
            state=SourceState.ACTIVE,
        )
        SessionSource.objects.create(
            session=self.session,
            source_id='camera-b',
            type=SourceType.CAMERA,
            name='Cam B',
            state=SourceState.ACTIVE,
        )
        SessionSource.objects.create(
            session=self.session,
            source_id='camera-c',
            type=SourceType.CAMERA,
            name='Cam C',
            state=SourceState.ACTIVE,
        )
        self.scene.sources_config = {
            'version': 2,
            'items': [
                {
                    'id': 'item-a',
                    'sceneId': str(self.scene.id),
                    'sourceId': 'camera-a',
                    'visible': True,
                    'zIndex': 0,
                },
                {
                    'id': 'item-b',
                    'sceneId': str(self.scene.id),
                    'sourceId': 'camera-b',
                    'visible': False,
                    'zIndex': 1,
                },
            ],
            'assignments': {'0': 'camera-a'},
        }
        self.scene.save(update_fields=['sources_config'])

        command = build_set_tile_order_command(self.session, scene=self.scene)
        self.assertEqual(
            command.hidden_source_ids,
            ['guest-hidden', 'camera-b', 'camera-c'],
        )
        self.assertNotIn('camera-a', command.hidden_source_ids)

    def test_empty_scene_hides_all_registry_sources_keeps_peers(self):
        SessionSource.objects.create(
            session=self.session,
            source_id='camera-a',
            type=SourceType.CAMERA,
            name='Cam A',
            state=SourceState.ACTIVE,
        )
        self.scene.sources_config = {
            'version': 2,
            'items': [],
            'assignments': {'0': 'host-peer'},
        }
        self.scene.save(update_fields=['sources_config'])

        command = build_set_tile_order_command(self.session, scene=self.scene)
        self.assertEqual(command.slot_assignments, {'0': 'host-peer'})
        self.assertEqual(
            command.hidden_source_ids,
            ['guest-hidden', 'camera-a'],
        )
