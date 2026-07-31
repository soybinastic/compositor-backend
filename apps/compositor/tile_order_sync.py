"""Build and dispatch tile-order commands to the compositor worker."""

from __future__ import annotations

from apps.compositor.commands import SetTileOrderCommand
from apps.compositor.compositor_pipeline import CompositorPipeline
from apps.compositor.tile_order import resolve_effective_assignments
from apps.scenes.models import StudioScene
from apps.sessions.models import StudioSession


def _resolve_scene_config(
    session: StudioSession,
    scene: StudioScene | None,
) -> dict | None:
    if scene is not None:
        return scene.sources_config
    active = getattr(session, 'active_scene', None)
    if active is not None:
        return active.sources_config
    return None


def build_set_tile_order_command(
    session: StudioSession,
    *,
    scene: StudioScene | None = None,
) -> SetTileOrderCommand:
    effective = resolve_effective_assignments(
        session.tile_order_config,
        _resolve_scene_config(session, scene),
    )
    slot_assignments = None
    if effective:
        slot_assignments = {
            str(slot): source_id for slot, source_id in sorted(effective.items())
        }

    return SetTileOrderCommand(
        session_id=str(session.id),
        host_peer_id=session.host_peer_id,
        slot_assignments=slot_assignments,
        hidden_source_ids=list(session.hidden_source_ids or []),
    )


def apply_tile_order_to_pipeline(
    pipeline: CompositorPipeline,
    session: StudioSession,
    *,
    scene: StudioScene | None = None,
) -> None:
    command = build_set_tile_order_command(session, scene=scene)
    pipeline.set_tile_order(
        host_peer_id=command.host_peer_id,
        slot_assignments=command.slot_assignments,
        hidden_source_ids=command.hidden_source_ids,
    )


def send_tile_order_command(
    session: StudioSession,
    *,
    scene: StudioScene | None = None,
) -> None:
    from apps.compositor.worker_manager import get_session_worker_manager

    worker_manager = get_session_worker_manager()
    if not worker_manager.is_running(str(session.id)):
        return
    worker_manager.send_command(build_set_tile_order_command(session, scene=scene))
