"""Tests for post-mixer gdkpixbufoverlay helpers."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from PIL import Image

from apps.graphics.constants import LAYER_BACKGROUND, LAYER_LOGO, LAYER_OVERLAY, LAYER_TICKER
from apps.graphics.post_mixer_overlays import (
    LAYER_GRAPHICS_STACK,
    POST_MIXER_OVERLAY_KEYS,
    PixbufLayerState,
    apply_pixbuf_to_overlay,
    clear_pixbuf_overlay,
    compose_static_stack,
    create_post_mixer_overlay_elements,
    image_to_pixbuf,
)


class PostMixerOverlayTests(SimpleTestCase):
    def test_overlay_key_order(self):
        self.assertEqual(
            POST_MIXER_OVERLAY_KEYS,
            (LAYER_GRAPHICS_STACK, LAYER_TICKER),
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
        # Margin keeps background.
        self.assertEqual(composed.getpixel((0, 0)), (0, 0, 255, 255))
        # Camera cutout is transparent so live video shows through.
        self.assertEqual(composed.getpixel((3, 3))[3], 0)
        # Logo still draws on top of the margin.
        self.assertEqual(composed.getpixel((7, 0))[:3], (255, 0, 0))

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
