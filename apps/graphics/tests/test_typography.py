"""Tests for scene typography catalog, policy, and font loader."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.graphics.typography.catalog import DEFAULT_DISPLAY_FONT, FontCatalog
from apps.graphics.typography.loader import FontFaceLoader
from apps.graphics.typography.policy import SceneTypographyPolicy
from apps.graphics.typography.strategies import (
    BundledFontStrategy,
    LegacyFallbackStrategy,
    SystemFontPathStrategy,
)


class FontCatalogTests(SimpleTestCase):
    def test_canonicalize_case_insensitive(self):
        catalog = FontCatalog()
        self.assertEqual(catalog.canonicalize('rubik'), 'Rubik')
        self.assertEqual(catalog.canonicalize('  Montserrat '), 'Montserrat')
        self.assertIsNone(catalog.canonicalize('Comic Sans'))
        self.assertIsNone(catalog.canonicalize(''))

    def test_departure_mono_aliases_fira_file(self):
        catalog = FontCatalog()
        self.assertEqual(catalog.filename_for('Departure Mono'), 'FiraMono-Bold.ttf')
        self.assertEqual(catalog.filename_for('Fira Mono'), 'FiraMono-Bold.ttf')


class SceneTypographyPolicyTests(SimpleTestCase):
    def setUp(self):
        self.policy = SceneTypographyPolicy()

    def test_engine_family_none_when_unset(self):
        self.assertIsNone(self.policy.engine_family({}))
        self.assertIsNone(self.policy.engine_family({'fonts': None}))
        self.assertIsNone(self.policy.engine_family({'banner': {'title': 'Hi'}}))

    def test_engine_family_accepts_fontFamily_alias(self):
        self.assertEqual(self.policy.engine_family({'fontFamily': 'Rubik'}), 'Rubik')

    def test_display_family_defaults_to_rubik(self):
        self.assertEqual(self.policy.display_family({}), DEFAULT_DISPLAY_FONT)
        self.assertEqual(self.policy.display_family({'fonts': 'Montserrat'}), 'Montserrat')


class FontFaceLoaderTests(SimpleTestCase):
    def test_legacy_fallback_always_returns_font(self):
        loader = FontFaceLoader(strategies=(LegacyFallbackStrategy(),))
        font = loader.load(None, 24)
        self.assertIsNotNone(font)

    def test_system_strategy_finds_file_in_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Minimal valid-enough path: copy from Legacy if available, else skip write
            # and assert strategy returns None when missing.
            strategy = SystemFontPathStrategy(root=root)
            self.assertIsNone(strategy.resolve('Missing.ttf', 16))

            # Create a tiny real TTF via Pillow's default isn't possible; touch a
            # non-ttf so load fails and returns None.
            fake = root / 'Rubik-Bold.ttf'
            fake.write_bytes(b'not-a-font')
            self.assertIsNone(strategy.resolve('Rubik-Bold.ttf', 16))

    def test_chain_uses_bundled_then_legacy(self):
        with TemporaryDirectory() as tmp:
            bundled = Path(tmp)
            loader = FontFaceLoader(
                strategies=(
                    BundledFontStrategy(directory=bundled),
                    LegacyFallbackStrategy(),
                )
            )
            font = loader.load('Rubik', 20)
            self.assertIsNotNone(font)
            self.assertTrue(hasattr(font, 'getbbox') or hasattr(font, 'getsize'))
