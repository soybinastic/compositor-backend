from abc import ABC, abstractmethod
from dataclasses import dataclass

from apps.layouts.types import LayoutType, ScaleMode


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
    scale_mode: ScaleMode = ScaleMode.CONTAIN


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


def resolve_host_id(source_ids: list[str], host_source_id: str | None) -> str:
    if host_source_id is not None and host_source_id in source_ids:
        return host_source_id
    return source_ids[0]
