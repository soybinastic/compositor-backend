"""Font resolution strategies (Chain of Responsibility)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from PIL import ImageFont

logger = logging.getLogger(__name__)


class FontResolutionStrategy(Protocol):
    def resolve(self, filename: str | None, size: int) -> ImageFont.ImageFont | None:
        """Return a loaded font, or None to try the next strategy."""


class SystemFontPathStrategy:
    """Load from /usr/share/fonts/truetype (staging) or a configurable root."""

    def __init__(self, root: str | Path = '/usr/share/fonts/truetype') -> None:
        self._root = Path(root)

    def resolve(self, filename: str | None, size: int) -> ImageFont.ImageFont | None:
        if not filename:
            return None
        path = self._root / filename
        if not path.is_file():
            # Some installs nest faces in subdirs — search one level.
            matches = list(self._root.glob(f'**/{filename}'))
            if not matches:
                return None
            path = matches[0]
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as exc:
            logger.debug('System font load failed for %s: %s', path, exc)
            return None


class BundledFontStrategy:
    """Load from apps/graphics/fonts/ next to the typography package."""

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            directory = Path(__file__).resolve().parent.parent / 'fonts'
        self._directory = Path(directory)

    def resolve(self, filename: str | None, size: int) -> ImageFont.ImageFont | None:
        if not filename:
            return None
        path = self._directory / filename
        if not path.is_file():
            return None
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as exc:
            logger.debug('Bundled font load failed for %s: %s', path, exc)
            return None


class LegacyFallbackStrategy:
    """Today's hard-coded Arial → DejaVu → Pillow default (always succeeds)."""

    _CANDIDATES = (
        '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    )

    def resolve(self, filename: str | None, size: int) -> ImageFont.ImageFont | None:
        for path in self._CANDIDATES:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()
