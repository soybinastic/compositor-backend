"""Tests for graphics constants, signatures, and geometry."""

from django.test import SimpleTestCase

from apps.graphics.constants import (
    ZORDER_BACKGROUND,
    ZORDER_BANNER_PRIMARY,
    ZORDER_CHAT,
    ZORDER_LOGO,
    ZORDER_OVERLAY,
    ZORDER_QR,
    ZORDER_TICKER,
)
from apps.graphics.constants import BANNER_TICKER_GAP, BANNER_X
from apps.graphics.geometry import (
    banner_bottom_inset,
    banner_layout,
    logo_geometry,
    overlay_geometry,
    qr_geometry,
    ticker_geometry,
)
from apps.graphics.gst_branches import content_signature, is_video_url
from apps.graphics.renderers.pil_overlays import (
    banner_bar_height,
    banner_content_width,
    ticker_bar_height,
)


class ZOrderTests(SimpleTestCase):
    def test_zorder_stacking(self):
        self.assertLess(ZORDER_BACKGROUND, ZORDER_TICKER)
        self.assertLess(ZORDER_TICKER, ZORDER_BANNER_PRIMARY)
        self.assertLess(ZORDER_BANNER_PRIMARY, ZORDER_OVERLAY)
        self.assertLess(ZORDER_OVERLAY, ZORDER_LOGO)
        self.assertLess(ZORDER_LOGO, ZORDER_CHAT)
        self.assertLess(ZORDER_CHAT, ZORDER_QR)


class SignatureTests(SimpleTestCase):
    def test_signature_stable(self):
        a = content_signature({'url': 'https://x/a.png', 'fit': 'cover'})
        b = content_signature({'fit': 'cover', 'url': 'https://x/a.png'})
        self.assertEqual(a, b)
        c = content_signature({'url': 'https://x/b.png', 'fit': 'cover'})
        self.assertNotEqual(a, c)

    def test_video_url_detection(self):
        self.assertTrue(is_video_url('https://cdn.example.com/clip.mp4'))
        self.assertTrue(is_video_url('https://cdn.example.com/a.webm?x=1'))
        self.assertFalse(is_video_url('https://cdn.example.com/still.png'))


class GeometryTests(SimpleTestCase):
    def test_logo_top_right_default(self):
        x, y, w, h = logo_geometry(1920, 1080, 700, 200, {})
        self.assertEqual(y, 20)
        self.assertLessEqual(w, 350)
        self.assertLessEqual(h, 100)
        self.assertEqual(x, 1920 - w - 20)

    def test_qr_center(self):
        x, y, w, h = qr_geometry(1920, 1080, {'position': 'center'})
        self.assertEqual(w, 250)
        self.assertEqual(h, 200)
        self.assertEqual(x, (1920 - w) // 2)

    def test_overlay_full_frame_default(self):
        self.assertEqual(overlay_geometry(1920, 1080, {}), (0, 0, 1920, 1080))


class BannerLayoutTests(SimpleTestCase):
    def test_dual_bar_gap_is_tight(self):
        primary_h = banner_bar_height(36)
        secondary_h = banner_bar_height(28)
        primary_geom, secondary_geom = banner_layout(
            1920,
            1080,
            bar_width=420,
            primary_height=primary_h,
            secondary_height=secondary_h,
            font_size=36,
            has_secondary=True,
        )
        assert secondary_geom is not None
        _, primary_y, _, _ = primary_geom
        _, secondary_y, _, _ = secondary_geom
        gap = secondary_y - (primary_y + primary_h)
        self.assertEqual(gap, 2)

    def test_content_width_shrink_wraps_short_text(self):
        width = banner_content_width(
            'Dr. Jane Smith',
            'Product Lead · Acme Co',
            36,
            canvas_w=1920,
        )
        self.assertLess(width, 1920 - BANNER_X * 2)
        self.assertGreaterEqual(width, 120)

    def test_single_bar_anchors_to_bottom_inset(self):
        primary_h = banner_bar_height(36)
        primary_geom, secondary_geom = banner_layout(
            1920,
            1080,
            bar_width=320,
            primary_height=primary_h,
            secondary_height=0,
            font_size=36,
            has_secondary=False,
        )
        self.assertIsNone(secondary_geom)
        _, primary_y, _, h = primary_geom
        self.assertEqual(primary_y + h, 1080 - 40)

    def test_bottom_ticker_clears_gap_above_ticker(self):
        primary_h = banner_bar_height(36)
        secondary_h = banner_bar_height(28)
        ticker_h = ticker_bar_height()
        inset = banner_bottom_inset(
            bottom_ticker_active=True,
            chat_active=False,
            ticker_bar_height=ticker_h,
        )
        _, secondary_geom = banner_layout(
            1920,
            1080,
            bar_width=420,
            primary_height=primary_h,
            secondary_height=secondary_h,
            font_size=36,
            has_secondary=True,
            bottom_inset=inset,
        )
        assert secondary_geom is not None
        _, ticker_y, _, _ = ticker_geometry(
            1920,
            1080,
            position='bottom',
            bar_height=ticker_h,
            chat_active=False,
        )
        _, secondary_y, _, secondary_bar_h = secondary_geom
        gap = ticker_y - (secondary_y + secondary_bar_h)
        self.assertEqual(gap, BANNER_TICKER_GAP)

    def test_banner_bottom_inset_without_ticker(self):
        self.assertEqual(
            banner_bottom_inset(
                bottom_ticker_active=False,
                chat_active=False,
                ticker_bar_height=ticker_bar_height(),
            ),
            40,
        )
