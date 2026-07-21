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
from apps.graphics.geometry import logo_geometry, overlay_geometry, qr_geometry
from apps.graphics.gst_branches import content_signature, is_video_url


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
