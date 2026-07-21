"""Layout strategy factory and pad geometry application."""

from __future__ import annotations

from apps.layouts.strategies.base import LayoutStrategy, Size, TileConfig
from apps.layouts.strategies.cinema import CinemaLayout
from apps.layouts.strategies.contain import ContainLayout
from apps.layouts.strategies.cover import CoverLayout
from apps.layouts.strategies.fullscreen import FullscreenLayout
from apps.layouts.strategies.grid import GridLayout
from apps.layouts.strategies.picture_in_picture import PictureInPictureLayout
from apps.layouts.strategies.side_by_side import SideBySideLayout
from apps.layouts.strategies.spotlight import SpotlightLayout
from apps.layouts.strategies.thumbnail import ThumbnailLayout
from apps.layouts.types import LayoutType

_LAYOUT_FACTORIES: dict[str, type[LayoutStrategy]] = {
    LayoutType.CONTAIN.value: ContainLayout,
    LayoutType.COVER.value: CoverLayout,
    LayoutType.THUMBNAIL.value: ThumbnailLayout,
    LayoutType.GRID.value: GridLayout,
    LayoutType.SIDE_BY_SIDE.value: SideBySideLayout,
    LayoutType.HALFSCREEN.value: SideBySideLayout,
    LayoutType.SPOTLIGHT.value: SpotlightLayout,
    LayoutType.CINEMA.value: CinemaLayout,
    LayoutType.PICTURE_IN_PICTURE.value: PictureInPictureLayout,
    LayoutType.OVERLAY.value: PictureInPictureLayout,
    LayoutType.FULLSCREEN.value: FullscreenLayout,
}


def get_layout_strategy(layout: str) -> LayoutStrategy:
    factory = _LAYOUT_FACTORIES.get(layout)
    if factory is None:
        raise ValueError(f'Unsupported layout: {layout}')
    return factory()


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
