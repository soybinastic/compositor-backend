"""Tests for background music scene config helpers."""

from django.test import TestCase

from apps.scenes.background_music import (
    normalize_background_music_config,
    normalize_background_music_track,
    validate_background_music_track_url,
)
from apps.scenes.constants import DEFAULT_BACKGROUND_MUSIC_CONFIG


class BackgroundMusicConfigTests(TestCase):
    def test_default_config_includes_muted(self):
        self.assertFalse(DEFAULT_BACKGROUND_MUSIC_CONFIG['muted'])

    def test_normalize_partial_config(self):
        config = normalize_background_music_config(
            {
                'enabled': True,
                'volume': 1.5,
                'track': {
                    'asset_id': 'abc',
                    'url': 'https://studio-assets.b-cdn.net/bgm/twilight_drift.mp3',
                    'title': 'Twilight Drift',
                },
                'muted': True,
            }
        )
        self.assertTrue(config['enabled'])
        self.assertTrue(config['muted'])
        self.assertEqual(config['volume'], 1.0)
        self.assertEqual(config['track']['asset_id'], 'abc')

    def test_normalize_track_from_source_alias(self):
        track = normalize_background_music_track(
            {
                'uuid': 'preset-1',
                'source': 'https://studio-assets.b-cdn.net/bgm/nebula_pulse.mp3',
                'title': 'Nebula Pulse',
            }
        )
        self.assertIsNotNone(track)
        assert track is not None
        self.assertEqual(track['asset_id'], 'preset-1')
        self.assertEqual(track['url'], 'https://studio-assets.b-cdn.net/bgm/nebula_pulse.mp3')

    def test_validate_track_url_rejects_unsupported_extension(self):
        with self.assertRaises(ValueError):
            validate_background_music_track_url('https://example.com/track.xyz')

    def test_empty_config_returns_defaults(self):
        config = normalize_background_music_config(None)
        self.assertEqual(config['version'], 1)
        self.assertIsNone(config['track'])
        self.assertFalse(config['muted'])
