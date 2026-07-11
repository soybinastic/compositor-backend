from django.test import TestCase

from apps.layouts.manager import LayoutManager
from apps.layouts.strategies.base import Size
from apps.sessions.models import LayoutType


class ContainLayoutTests(TestCase):
    def test_single_participant_fills_canvas(self):
        manager = LayoutManager.for_layout(LayoutType.CONTAIN, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['peer-a'])

        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0].x, 0)
        self.assertEqual(tiles[0].y, 0)
        self.assertEqual(tiles[0].width, 1920)
        self.assertEqual(tiles[0].height, 1080)

    def test_four_participants_form_2x2_grid(self):
        manager = LayoutManager.for_layout(LayoutType.CONTAIN, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['a', 'b', 'c', 'd'])
        tile_map = {tile.source_id: tile for tile in tiles}

        self.assertEqual(tile_map['a'].width, 960)
        self.assertEqual(tile_map['a'].height, 540)
        self.assertEqual(tile_map['b'].x, 960)
        self.assertEqual(tile_map['c'].y, 540)


class ThumbnailLayoutTests(TestCase):
    def test_host_gets_main_area(self):
        manager = LayoutManager.for_layout(LayoutType.THUMBNAIL, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')
        tile_map = {tile.source_id: tile for tile in tiles}

        self.assertEqual(tile_map['host'].width, 1920)
        self.assertEqual(tile_map['host'].height, 864)
        self.assertEqual(tile_map['guest'].y, 864)
        self.assertEqual(tile_map['guest'].height, 216)

    def test_first_participant_becomes_host_when_unspecified(self):
        manager = LayoutManager.for_layout(LayoutType.THUMBNAIL, Size(width=1000, height=500))
        tiles = manager.compute_tiles(['first', 'second'])
        host_tile = tiles[0]

        self.assertEqual(host_tile.source_id, 'first')
        self.assertEqual(host_tile.height, 400)


class LayoutManagerTests(TestCase):
    def test_hot_swap_layout_strategy(self):
        manager = LayoutManager.for_layout(LayoutType.CONTAIN, Size(width=1920, height=1080))
        contain_tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')

        manager.set_strategy(LayoutType.THUMBNAIL)
        thumbnail_tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')

        self.assertNotEqual(contain_tiles[0].height, thumbnail_tiles[0].height)
