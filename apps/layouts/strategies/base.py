from abc import ABC, abstractmethod
from dataclasses import dataclass

from apps.layouts.types import LayoutType


@dataclass(frozen=True)
class Size:
    width: int
    height: int


@dataclass(frozen=True)
class TileConfig:
    source_id: str
    x: int
    y: int
    width: int
    height: int
    zorder: int = 0


class LayoutStrategy(ABC):
    """Strategy interface for computing participant tile positions."""

    layout_type: LayoutType

    @abstractmethod
    def compute_tiles(
        self,
        source_ids: list[str],
        canvas: Size,
        host_source_id: str | None = None,
    ) -> list[TileConfig]:
        """Return tile geometry for each video source on the canvas."""
