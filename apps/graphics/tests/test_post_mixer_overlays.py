"""Tests for post-mixer gdkpixbufoverlay helpers."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from PIL import Image

from apps.graphics.constants import LAYER_BACKGROUND, LAYER_COUNTDOWN, LAYER_LOGO, LAYER_OVERLAY, LAYER_TICKER
from apps.graphics.post_mixer_overlays import (
    BACKGROUND_TILE_INNER_GAP,
    BACKGROUND_TILE_INSET,
    LAYER_GRAPHICS_STACK,
    POST_MIXER_OVERLAY_KEYS,
    PixbufLayerState,
    apply_directional_background_insets,
    apply_pixbuf_to_overlay,
    clear_pixbuf_overlay,
    compose_static_stack,
    create_post_mixer_overlay_elements,
    image_to_pixbuf,
)
from apps.layouts.strategies.base import TileConfig
from apps.layouts.types import ScaleMode


class PostMixerOverlayTests(SimpleTestCase):
    def test_overlay_key_order(self):
        self.assertEqual(
            POST_MIXER_OVERLAY_KEYS,
            (LAYER_GRAPHICS_STACK, LAYER_TICKER, LAYER_COUNTDOWN),
        )

    def test_image_to_pixbuf_roundtrip_size(self):
        image = Image.new('RGBA', (32, 16), (255, 0, 0, 128))
        pixbuf, raw = image_to_pixbuf(image)
        self.assertEqual(pixbuf.get_width(), 32)
        self.assertEqual(pixbuf.get_height(), 16)
        self.assertEqual(len(raw), 32 * 16 * 4)

    def test_compose_static_stack_layers(self):
        layers = {
            LAYER_OVERLAY: PixbufLayerState(
                layer_key=LAYER_OVERLAY,
                geometry=(0, 0, 4, 4),
                visible=True,
                _image=Image.new('RGBA', (4, 4), (255, 0, 0, 255)),
            ),
            LAYER_LOGO: PixbufLayerState(
                layer_key=LAYER_LOGO,
                geometry=(1, 1, 2, 2),
                visible=True,
                _image=Image.new('RGBA', (2, 2), (0, 255, 0, 255)),
            ),
        }
        composed = compose_static_stack(layers, canvas_w=4, canvas_h=4)
        self.assertIsNotNone(composed)
        assert composed is not None
        self.assertEqual(composed.size, (4, 4))
        self.assertEqual(composed.getpixel((0, 0))[:3], (255, 0, 0))
        self.assertEqual(composed.getpixel((1, 1))[:3], (0, 255, 0))

    def test_compose_background_punches_video_cutouts(self):
        layers = {
            LAYER_BACKGROUND: PixbufLayerState(
                layer_key=LAYER_BACKGROUND,
                geometry=(0, 0, 8, 8),
                visible=True,
                _image=Image.new('RGBA', (8, 8), (0, 0, 255, 255)),
            ),
            LAYER_LOGO: PixbufLayerState(
                layer_key=LAYER_LOGO,
                geometry=(7, 0, 1, 1),
                visible=True,
                _image=Image.new('RGBA', (1, 1), (255, 0, 0, 255)),
            ),
        }
        composed = compose_static_stack(
            layers,
            canvas_w=8,
            canvas_h=8,
            video_cutouts=[(2, 2, 4, 4)],
        )
        self.assertIsNotNone(composed)
        assert composed is not None
        # Margin keeps background (margin-strip path for single cutout).
        self.assertEqual(composed.getpixel((0, 0)), (0, 0, 255, 255))
        # Camera cutout is transparent so live video shows through.
        self.assertEqual(composed.getpixel((3, 3))[3], 0)
        # Logo still draws on top of the margin.
        self.assertEqual(composed.getpixel((7, 0))[:3], (255, 0, 0))

    def test_compose_background_multi_cutout_clears_tiles(self):
        layers = {
            LAYER_BACKGROUND: PixbufLayerState(
                layer_key=LAYER_BACKGROUND,
                geometry=(0, 0, 8, 8),
                visible=True,
                _image=Image.new('RGBA', (8, 8), (0, 255, 0, 255)),
            ),
        }
        composed = compose_static_stack(
            layers,
            canvas_w=8,
            canvas_h=8,
            video_cutouts=[(0, 0, 3, 3), (5, 5, 3, 3)],
        )
        self.assertIsNotNone(composed)
        assert composed is not None
        self.assertEqual(composed.getpixel((1, 1))[3], 0)
        self.assertEqual(composed.getpixel((6, 6))[3], 0)
        self.assertEqual(composed.getpixel((4, 0)), (0, 255, 0, 255))

    def test_apply_and_clear_sets_alpha(self):
        element = MagicMock()
        element.find_property.return_value = MagicMock()
        state = PixbufLayerState(layer_key=LAYER_TICKER)
        image = Image.new('RGBA', (8, 8), (0, 255, 0, 255))
        apply_pixbuf_to_overlay(element, image, (10, 20, 8, 8), state=state)
        element.set_property.assert_any_call('alpha', 1.0)
        self.assertTrue(state.visible)
        clear_pixbuf_overlay(element, state)
        element.set_property.assert_any_call('alpha', 0.0)
        self.assertFalse(state.visible)

    def test_create_elements_requires_plugin(self):
        elements = create_post_mixer_overlay_elements()
        self.assertEqual(set(elements.keys()), set(POST_MIXER_OVERLAY_KEYS))
        for element in elements.values():
            self.assertIsNotNone(element)


class DirectionalBackgroundInsetTests(SimpleTestCase):
    def _tile(self, source_id: str, x: int, y: int, width: int, height: int) -> TileConfig:
        return TileConfig(
            source_id=source_id,
            x=x,
            y=y,
            width=width,
            height=height,
            zorder=1,
            scale_mode=ScaleMode.COVER,
        )

    def test_single_tile_uses_outer_inset_on_all_sides(self):
        tiles = apply_directional_background_insets(
            [self._tile('host', 0, 0, 1920, 1080)],
        )
        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0].x, BACKGROUND_TILE_INSET)
        self.assertEqual(tiles[0].y, BACKGROUND_TILE_INSET)
        self.assertEqual(tiles[0].width, 1920 - 2 * BACKGROUND_TILE_INSET)
        self.assertEqual(tiles[0].height, 1080 - 2 * BACKGROUND_TILE_INSET)

    def test_two_horizontal_tiles_use_inner_gap_in_center(self):
        tiles = apply_directional_background_insets(
            [
                self._tile('left', 0, 0, 960, 1080),
                self._tile('right', 960, 0, 960, 1080),
            ],
        )
        left, right = tiles
        self.assertEqual(left.x, BACKGROUND_TILE_INSET)
        self.assertEqual(left.width, 960 - BACKGROUND_TILE_INSET - BACKGROUND_TILE_INNER_GAP)
        self.assertEqual(right.x, 960 + BACKGROUND_TILE_INNER_GAP)
        self.assertEqual(
            right.width,
            960 - BACKGROUND_TILE_INSET - BACKGROUND_TILE_INNER_GAP,
        )
        center_gap = right.x - (left.x + left.width)
        self.assertEqual(center_gap, 2 * BACKGROUND_TILE_INNER_GAP)

    def test_four_tile_grid_uses_inner_gaps_between_neighbors(self):
        tiles = apply_directional_background_insets(
            [
                self._tile('a', 0, 0, 960, 540),
                self._tile('b', 960, 0, 960, 540),
                self._tile('c', 0, 540, 960, 540),
                self._tile('d', 960, 540, 960, 540),
            ],
        )
        by_id = {tile.source_id: tile for tile in tiles}
        self.assertEqual(by_id['a'].x, BACKGROUND_TILE_INSET)
        self.assertEqual(by_id['a'].y, BACKGROUND_TILE_INSET)
        self.assertEqual(by_id['b'].x, 960 + BACKGROUND_TILE_INNER_GAP)
        self.assertEqual(by_id['c'].y, 540 + BACKGROUND_TILE_INNER_GAP)
        horizontal_gap = by_id['b'].x - (by_id['a'].x + by_id['a'].width)
        vertical_gap = by_id['c'].y - (by_id['a'].y + by_id['a'].height)
        self.assertEqual(horizontal_gap, 2 * BACKGROUND_TILE_INNER_GAP)
        self.assertEqual(vertical_gap, 2 * BACKGROUND_TILE_INNER_GAP)
