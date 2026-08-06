"""Scene typography policy: engine vs display family resolution."""

from __future__ import annotations

from typing import Any, Mapping

from apps.graphics.typography.catalog import DEFAULT_DISPLAY_FONT, FontCatalog


class SceneTypographyPolicy:
    """
    Interprets graphics_config.fonts for consumers.

    - engine_family: None when unset → burn-in uses legacy Arial/DejaVu (no look change).
    - display_family: Rubik when unset → picker / preview WYSIWYG default.
    """

    def __init__(self, catalog: FontCatalog | None = None) -> None:
        self._catalog = catalog or FontCatalog()

    @property
    def catalog(self) -> FontCatalog:
        return self._catalog

    def raw_fonts_value(self, state: Mapping[str, Any] | None) -> str | None:
        if not state:
            return None
        value = state.get('fonts')
        if value is None or value == '':
            value = state.get('fontFamily')
        if value is None or value == '':
            return None
        return str(value)

    def engine_family(self, state: Mapping[str, Any] | None) -> str | None:
        """Canonical family for program output, or None to keep legacy burn-in."""
        return self._catalog.canonicalize(self.raw_fonts_value(state))

    def display_family(self, state: Mapping[str, Any] | None) -> str:
        """Family for UI picker / preview (defaults to Rubik)."""
        canonical = self.engine_family(state)
        if canonical is not None:
            return canonical
        return self._catalog.canonicalize(self._catalog.default_display) or DEFAULT_DISPLAY_FONT
