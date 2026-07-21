"""Unit tests for graphics visibility and state merge."""

from django.test import SimpleTestCase, override_settings

from apps.graphics.state import empty_graphics_state, merge_graphics_state
from apps.graphics.visibility import (
    background_should_show,
    banner_preserve_on_missing_flag,
    banner_should_show,
    chat_should_show,
    is_http_image_url,
    logo_should_show,
    overlay_should_show,
    qr_should_show,
    ticker_should_show,
)


class VisibilityTests(SimpleTestCase):
    def test_background_requires_contain_or_fullscreen_and_url(self):
        config = {'url': 'https://cdn.example.com/bg.png'}
        self.assertTrue(background_should_show(config, 'CONTAIN'))
        self.assertTrue(background_should_show(config, 'FULLSCREEN'))
        self.assertFalse(background_should_show(config, 'GRID'))
        self.assertFalse(background_should_show({}, 'CONTAIN'))

    @override_settings(COMPOSITOR_DISABLE_BACKGROUND=True)
    def test_background_env_disable(self):
        config = {'url': 'https://cdn.example.com/bg.png'}
        self.assertFalse(background_should_show(config, 'CONTAIN'))

    def test_overlay_requires_active_and_url(self):
        self.assertFalse(overlay_should_show({'url': 'https://x/a.png', 'is_active': False}))
        self.assertTrue(overlay_should_show({'url': 'https://x/a.png', 'is_active': True}))

    def test_logo_requires_active_and_url(self):
        self.assertFalse(logo_should_show({'source': 'https://x/l.png', 'is_active': False}))
        self.assertTrue(logo_should_show({'source': 'https://x/l.png', 'is_active': True}))

    def test_qr_requires_shown_and_http_url(self):
        self.assertFalse(qr_should_show({'url': 'https://x/q.png', 'is_shown': False}))
        self.assertFalse(qr_should_show({'url': 'ftp://x/q.png', 'is_shown': True}))
        self.assertTrue(qr_should_show({'url': 'https://x/q.png', 'is_shown': True}))
        self.assertTrue(is_http_image_url('https://cdn.example.com/qr.png'))

    def test_banner_display_flag_and_text(self):
        self.assertFalse(
            banner_should_show({'is_display': False, 'title': 'Hello'})
        )
        self.assertTrue(
            banner_should_show({'is_display_names': True, 'title': 'Hello'})
        )
        self.assertTrue(banner_should_show({'title': 'Only title'}))

    def test_banner_preserve_when_flag_missing(self):
        existing = {'title': 'Keep', 'is_display': True}
        incoming = {'title': 'Ignored without flag'}
        preserved = banner_preserve_on_missing_flag(incoming, existing)
        self.assertEqual(preserved, existing)

        cleared = banner_preserve_on_missing_flag(
            {'is_display': False, 'title': 'X'},
            existing,
        )
        self.assertEqual(cleared['is_display'], False)

    def test_ticker_enabled_and_text(self):
        self.assertFalse(ticker_should_show({'tickerText': '', 'tickerEnabled': True}))
        self.assertFalse(ticker_should_show({'tickerText': 'Hi', 'tickerEnabled': False}))
        self.assertTrue(ticker_should_show({'tickerText': 'Hi'}))

    def test_chat_enabled(self):
        self.assertFalse(chat_should_show({'enabled': False, 'messages': []}))
        self.assertTrue(chat_should_show({'enabled': True, 'messages': []}))


class StateMergeTests(SimpleTestCase):
    def test_merge_leaves_missing_keys_unchanged(self):
        current = empty_graphics_state()
        current['logo'] = {'url': 'https://x/l.png', 'is_active': True}
        merged = merge_graphics_state(current, {'ticker': {'tickerText': 'Go'}})
        self.assertEqual(merged['logo']['url'], 'https://x/l.png')
        self.assertEqual(merged['ticker']['tickerText'], 'Go')
        self.assertIsNone(merged['background'])

    def test_merge_explicit_none_clears_layer(self):
        current = empty_graphics_state()
        current['overlay'] = {'url': 'https://x/o.png', 'is_active': True}
        merged = merge_graphics_state(current, {'overlay': None})
        self.assertIsNone(merged['overlay'])
