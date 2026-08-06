"""FontFaceLoader facade over the resolution strategy chain."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Sequence

from PIL import ImageFont

from apps.graphics.typography.catalog import FontCatalog
from apps.graphics.typography.strategies import (
    BundledFontStrategy,
    FontResolutionStrategy,
    LegacyFallbackStrategy,
    SystemFontPathStrategy,
)

logger = logging.getLogger(__name__)


class FontFaceLoader:
    """Load PIL fonts by family name; strategies tried in order until one succeeds."""

    def __init__(
        self,
        catalog: FontCatalog | None = None,
        strategies: Sequence[FontResolutionStrategy] | None = None,
    ) -> None:
        self._catalog = catalog or FontCatalog()
        self._strategies: tuple[FontResolutionStrategy, ...] = tuple(
            strategies
            if strategies is not None
            else (
                SystemFontPathStrategy(),
                BundledFontStrategy(),
                LegacyFallbackStrategy(),
            )
        )

    @property
    def catalog(self) -> FontCatalog:
        return self._catalog

    def load(self, family: str | None, size: int) -> ImageFont.ImageFont:
        """
        Load a face for ``family`` at ``size``.

        When ``family`` is None/unknown, skip catalog filename and go straight
        to the strategy chain with ``filename=None`` so LegacyFallback still runs.
        """
        size = max(1, int(size))
        filename = self._catalog.filename_for(family) if family else None
        if family and filename is None:
            logger.debug('Unknown font family %r — using legacy fallback', family)
        for strategy in self._strategies:
            font = strategy.resolve(filename, size)
            if font is not None:
                return font
        return ImageFont.load_default()


@lru_cache(maxsize=1)
def create_default_font_loader() -> FontFaceLoader:
    return FontFaceLoader()
