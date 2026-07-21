"""Controller unit tests with mocked pipeline owner."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.graphics.constants import LAYER_BACKGROUND, LAYER_LOGO, ZORDER_LOGO
from apps.graphics.controller import GraphicsController
from apps.graphics.gst_branches import GraphicBranch


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
        owner._link_sequential = MagicMock()
        owner._set_pad_property_if_present = MagicMock()
        owner._hide_pad = MagicMock()
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

    def test_signature_skips_rebuild_for_logo(self):
        owner = self._owner()
        controller = GraphicsController(owner)
        pad = MagicMock()
        from apps.graphics.gst_branches import content_signature

        pre_sig = content_signature(
            {
                'url': 'https://cdn.example.com/logo.png',
                'placement': 'top-right',
                'layer': LAYER_LOGO,
            }
        )
        branch = GraphicBranch(
            layer_key=LAYER_LOGO,
            compositor_sink_pad=pad,
            signature=pre_sig + 'extra',
            geometry=(1500, 20, 100, 40),
            zorder=ZORDER_LOGO,
        )
        controller._branches[LAYER_LOGO] = branch
        config = {
            'url': 'https://cdn.example.com/logo.png',
            'is_active': True,
            'placement': 'top-right',
        }

        with patch(
            'apps.graphics.controller.download_and_prepare_still'
        ) as download, patch.object(controller, '_attach_still_image') as attach:
            controller._apply_logo(config)
            download.assert_not_called()
            attach.assert_not_called()
            owner._set_pad_property_if_present.assert_called()
