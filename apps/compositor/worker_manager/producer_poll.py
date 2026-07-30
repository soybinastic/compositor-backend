"""Helper to wire producer polling into session workers."""

from __future__ import annotations

from apps.compositor.commands import SyncProducersCommand
from apps.compositor.session_producer_poller import start_session_producer_poller
from apps.compositor.worker_manager.command_executor import SessionCommandExecutor
from integrations.mediasoup.client import MediasoupHttpClient


def attach_producer_poller(
    session_id: str,
    room_id: str,
    executor: SessionCommandExecutor,
    *,
    client: MediasoupHttpClient | None = None,
) -> None:
    def sync_producers(peer_producers_infos: list) -> None:
        executor.submit_command(
            SyncProducersCommand(
                session_id=session_id,
                peer_producers_infos=peer_producers_infos,
            )
        )

    start_session_producer_poller(
        session_id,
        room_id,
        sync_producers=sync_producers,
        client=client,
    )
