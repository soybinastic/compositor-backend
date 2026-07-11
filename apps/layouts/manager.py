"""Layout strategy factory and pad geometry application."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.strategies.contain import ContainLayout
from apps.layouts.strategies.thumbnail import ThumbnailLayout
from apps.layouts.types import LayoutType
from apps.sessions.models import LayoutType as SessionLayoutType


def get_layout_strategy(layout: str) -> LayoutStrategy:
    if layout == LayoutType.CONTAIN or layout == SessionLayoutType.CONTAIN:
        return ContainLayout()
    if layout == LayoutType.THUMBNAIL or layout == SessionLayoutType.THUMBNAIL:
        return ThumbnailLayout()
    raise ValueError(f'Unsupported layout: {layout}')


class LayoutManager:
    """Computes tile geometry for a layout strategy."""

    def __init__(self, strategy: LayoutStrategy, canvas: Size) -> None:
        self._strategy = strategy
        self._canvas = canvas

    @classmethod
    def for_layout(cls, layout: str, canvas: Size) -> LayoutManager:
        return cls(get_layout_strategy(layout), canvas)

    def compute_tiles(
        self,
        source_ids: list[str],
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        return self._strategy.compute_tiles(
            source_ids,
            self._canvas,
            host_source_id=host_source_id,
        )

    def set_strategy(self, layout: str) -> None:
        self._strategy = get_layout_strategy(layout)
