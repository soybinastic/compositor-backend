"""Controller unit tests with mocked pipeline owner."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from PIL import Image

from apps.graphics.constants import LAYER_BACKGROUND, LAYER_LOGO
from apps.graphics.controller import GraphicsController
from apps.graphics.post_mixer_overlays import LAYER_GRAPHICS_STACK, PixbufLayerState


class GraphicsControllerTests(SimpleTestCase):
    def _owner(self):
        owner = MagicMock()
        owner.width = 1920
        owner.height = 1080
        owner.fps = 30
        owner.session_id = 'abcd1234-session'
        owner._pipeline = MagicMock()
        owner._compositor = MagicMock()
        owner._video_mix_backend = MagicMock()
        owner._video_mix_backend.build_ingest_tail.return_value = [MagicMock(name='queue')]
        owner._video_mix_backend.build_graphics_ingest_tail.return_value = [
            MagicMock(name='gfx_queue')
        ]
        owner._link_sequential = MagicMock()
        owner._set_pad_property_if_present = MagicMock()
        owner._hide_pad = MagicMock()
        owner._make_running_time_offset_probe = MagicMock(return_value=MagicMock())
        stack_el = MagicMock(name='graphics_stack')
        stack_el.find_property.return_value = MagicMock()
        owner._post_mixer_overlays = {LAYER_GRAPHICS_STACK: stack_el}
        return owner

    def test_layout_only_skips_overlay_rebuild(self):
        owner = self._owner()
        controller = GraphicsController(owner)
        with patch.object(controller, '_apply_background') as apply_bg, patch.object(
            controller, '_apply_overlay'
        ) as apply_overlay, patch.object(
            controller, 'sync_background_visibility'
        ) as sync_bg:
            controller.apply_state(
                {LAYER_BACKGROUND: {'url': 'https://x/bg.png'}},
                layout='GRID',
                layout_only=True,
            )
            sync_bg.assert_called_once_with('GRID')
            apply_bg.assert_not_called()
            apply_overlay.assert_not_called()

    def test_set_video_cutouts_rebuilds_when_background_active(self):
        owner = self._owner()
        controller = GraphicsController(owner)
        controller._pixbuf_layers[LAYER_BACKGROUND] = PixbufLayerState(
            layer_key=LAYER_BACKGROUND,
            geometry=(0, 0, 1920, 1080),
            visible=True,
            _image=Image.new('RGBA', (8, 8), (0, 0, 255, 255)),
        )
        with patch.object(controller, '_rebuild_graphics_stack') as rebuild:
            controller.set_video_cutouts([(48, 48, 1824, 984)])
            rebuild.assert_called_once()
            self.assertEqual(controller._video_cutouts, [(48, 48, 1824, 984)])

    def test_apply_background_on_contain_uses_post_mixer_stack(self):
        owner = self._owner()
        controller = GraphicsController(owner)
        image = Image.new('RGBA', (64, 64), (10, 20, 30, 255))
        with patch(
            'apps.graphics.controller.download_and_prepare_still',
            return_value=image,
        ), patch.object(controller, '_rebuild_graphics_stack') as rebuild:
            controller._apply_background(
                {'url': 'https://cdn.example.com/bg.png', 'fit': 'cover'},
                'CONTAIN',
            )
            rebuild.assert_called()
            self.assertTrue(controller.background_active)
            bg = controller._pixbuf_layers[LAYER_BACKGROUND]
            self.assertEqual(bg.geometry, (0, 0, 1920, 1080))

    def test_signature_skips_rebuild_for_logo(self):
        owner = self._owner()
        controller = GraphicsController(owner)
        from apps.graphics.gst_branches import content_signature

        pre_sig = content_signature(
            {
                'url': 'https://cdn.example.com/logo.png',
                'placement': 'top-right',
                'layer': LAYER_LOGO,
            }
        )
        controller._pixbuf_layers[LAYER_LOGO] = PixbufLayerState(
            layer_key=LAYER_LOGO,
            signature=pre_sig + 'extra',
            geometry=(1500, 20, 100, 40),
            visible=True,
            _image=Image.new('RGBA', (100, 40), (255, 0, 0, 255)),
        )
        config = {
            'url': 'https://cdn.example.com/logo.png',
            'is_active': True,
            'placement': 'top-right',
        }

        with patch(
            'apps.graphics.controller.download_and_prepare_still'
        ) as download, patch.object(controller, '_rebuild_graphics_stack') as rebuild:
            controller._apply_logo(config)
            download.assert_not_called()
            rebuild.assert_called()
