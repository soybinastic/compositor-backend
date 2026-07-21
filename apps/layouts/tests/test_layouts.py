from django.test import TestCase

from apps.layouts.manager import LayoutManager, get_layout_strategy
from apps.layouts.strategies.base import Size
from apps.layouts.types import LayoutType, ScaleMode
from apps.sessions.models import LayoutType as SessionLayoutType


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


class CoverLayoutTests(TestCase):
    def test_cover_matches_contain_geometry_with_cover_scale(self):
        contain = LayoutManager.for_layout(LayoutType.CONTAIN, Size(width=1920, height=1080))
        cover = LayoutManager.for_layout(LayoutType.COVER, Size(width=1920, height=1080))
        sources = ['a', 'b', 'c']

        contain_tiles = {t.source_id: t for t in contain.compute_tiles(sources)}
        cover_tiles = {t.source_id: t for t in cover.compute_tiles(sources)}

        for source_id in sources:
            self.assertEqual(cover_tiles[source_id].x, contain_tiles[source_id].x)
            self.assertEqual(cover_tiles[source_id].width, contain_tiles[source_id].width)
            self.assertEqual(cover_tiles[source_id].scale_mode, ScaleMode.COVER)
            self.assertEqual(contain_tiles[source_id].scale_mode, ScaleMode.CONTAIN)


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


class GridLayoutTests(TestCase):
    def test_four_or_fewer_use_2x2(self):
        manager = LayoutManager.for_layout(LayoutType.GRID, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['a', 'b', 'c'])
        self.assertEqual(tiles[0].width, 960)
        self.assertEqual(tiles[0].height, 540)
        self.assertEqual(tiles[2].x, 0)
        self.assertEqual(tiles[2].y, 540)

    def test_five_or_more_use_3x3_and_cap_at_nine(self):
        manager = LayoutManager.for_layout(LayoutType.GRID, Size(width=1920, height=1080))
        sources = [f'p{i}' for i in range(12)]
        tiles = manager.compute_tiles(sources)
        self.assertEqual(len(tiles), 9)
        self.assertEqual(tiles[0].width, 640)
        self.assertEqual(tiles[0].height, 360)


class SideBySideLayoutTests(TestCase):
    def test_two_sources_split_evenly(self):
        manager = LayoutManager.for_layout(LayoutType.SIDE_BY_SIDE, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['left', 'right', 'ignored'])
        tile_map = {tile.source_id: tile for tile in tiles}

        self.assertEqual(len(tiles), 2)
        self.assertEqual(tile_map['left'].width, 960)
        self.assertEqual(tile_map['right'].x, 960)
        self.assertEqual(tile_map['right'].width, 960)

    def test_halfscreen_alias_uses_same_strategy(self):
        side = get_layout_strategy(LayoutType.SIDE_BY_SIDE.value)
        half = get_layout_strategy(LayoutType.HALFSCREEN.value)
        self.assertEqual(type(side), type(half))


class SpotlightLayoutTests(TestCase):
    def test_host_left_others_right_strip(self):
        manager = LayoutManager.for_layout(LayoutType.SPOTLIGHT, Size(width=1000, height=600))
        tiles = manager.compute_tiles(['host', 'g1', 'g2'], host_source_id='host')
        tile_map = {tile.source_id: tile for tile in tiles}

        self.assertEqual(tile_map['host'].width, 700)
        self.assertEqual(tile_map['host'].height, 600)
        self.assertEqual(tile_map['g1'].x, 700)
        self.assertEqual(tile_map['g1'].width, 300)
        self.assertEqual(tile_map['g1'].height, 300)
        self.assertEqual(tile_map['g2'].y, 300)


class CinemaLayoutTests(TestCase):
    def test_filmstrip_with_gaps(self):
        manager = LayoutManager.for_layout(LayoutType.CINEMA, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['host', 'g1', 'g2'], host_source_id='host')
        tile_map = {tile.source_id: tile for tile in tiles}

        self.assertEqual(tile_map['host'].height, 810)
        self.assertGreater(tile_map['g1'].y, 810)
        self.assertGreater(tile_map['g1'].x, 0)
        self.assertNotEqual(tile_map['g1'].x, tile_map['g2'].x)


class PictureInPictureLayoutTests(TestCase):
    def test_host_full_frame_guest_bottom_right(self):
        manager = LayoutManager.for_layout(
            LayoutType.PICTURE_IN_PICTURE,
            Size(width=1920, height=1080),
        )
        tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')
        tile_map = {tile.source_id: tile for tile in tiles}

        self.assertEqual(tile_map['host'].width, 1920)
        self.assertEqual(tile_map['host'].height, 1080)
        self.assertEqual(tile_map['host'].zorder, 1)
        self.assertGreater(tile_map['guest'].zorder, tile_map['host'].zorder)
        self.assertGreater(tile_map['guest'].x, 1000)
        self.assertGreater(tile_map['guest'].y, 700)

    def test_overlay_alias(self):
        pip = get_layout_strategy(LayoutType.PICTURE_IN_PICTURE.value)
        overlay = get_layout_strategy(LayoutType.OVERLAY.value)
        self.assertEqual(type(pip), type(overlay))


class FullscreenLayoutTests(TestCase):
    def test_only_host_is_tiled(self):
        manager = LayoutManager.for_layout(LayoutType.FULLSCREEN, Size(width=1920, height=1080))
        tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')

        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0].source_id, 'host')
        self.assertEqual(tiles[0].width, 1920)
        self.assertEqual(tiles[0].height, 1080)


class LayoutManagerTests(TestCase):
    def test_hot_swap_layout_strategy(self):
        manager = LayoutManager.for_layout(LayoutType.CONTAIN, Size(width=1920, height=1080))
        contain_tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')

        manager.set_strategy(LayoutType.THUMBNAIL)
        thumbnail_tiles = manager.compute_tiles(['host', 'guest'], host_source_id='host')

        self.assertNotEqual(contain_tiles[0].height, thumbnail_tiles[0].height)

    def test_session_layout_choices_are_registered(self):
        for value, _label in SessionLayoutType.choices:
            strategy = get_layout_strategy(value)
            self.assertIsNotNone(strategy)
