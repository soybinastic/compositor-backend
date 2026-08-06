"""Registry of supported scene font families and their TTF filenames."""

from __future__ import annotations

from dataclasses import dataclass, field

# Display default when host has not selected a font (UI / preview only).
DEFAULT_DISPLAY_FONT = 'Rubik'

# Must match filenames available under /usr/share/fonts/truetype (staging)
# or apps/graphics/fonts/ (bundled).
FONT_FAMILY_FILES: dict[str, str] = {
    'Arial': 'Arial Bold.ttf',
    'Rubik': 'Rubik-Bold.ttf',
    'Roboto Slab': 'RobotoSlab-Bold.ttf',
    'Open Sans': 'OpenSans_Condensed-Bold.ttf',
    'Bricolage Grotesque': 'BricolageGrotesque-VariableFont_opsz,wdth,wght.ttf',
    'Montserrat': 'Montserrat-Bold.ttf',
    'Shantell Sans': 'ShantellSans-Bold.ttf',
    'Fira Mono': 'FiraMono-Bold.ttf',
    'Permanent Marker': 'PermanentMarker-Regular.ttf',
    'Geologica': 'Geologica_Auto-Bold.ttf',
    'Graphik': 'Graphik-Bold.ttf',
    'Dyna Puff': 'DynaPuff-Regular.ttf',
    'Departure Mono': 'FiraMono-Bold.ttf',
}


@dataclass(frozen=True)
class FontCatalog:
    """Maps canonical family names to TTF filenames (Open/Closed: add entries here)."""

    files: dict[str, str] = field(default_factory=lambda: dict(FONT_FAMILY_FILES))
    default_display: str = DEFAULT_DISPLAY_FONT

    def families(self) -> tuple[str, ...]:
        return tuple(self.files.keys())

    def canonicalize(self, name: str | None) -> str | None:
        """Return canonical family key, or None if unknown / empty."""
        if name is None:
            return None
        text = str(name).strip()
        if not text:
            return None
        if text in self.files:
            return text
        lower = text.lower()
        for key in self.files:
            if key.lower() == lower:
                return key
        return None

    def filename_for(self, family: str | None) -> str | None:
        canonical = self.canonicalize(family)
        if canonical is None:
            return None
        return self.files.get(canonical)
