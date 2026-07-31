"""Session worker subprocess loop (GStreamer + GLib executor)."""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings

from apps.compositor.commands import CommandResult, SessionCommand
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.worker_manager.command_codec import (
    decode_command,
    encode_command,
    encode_result,
)
from apps.compositor.worker_manager.command_executor import SessionCommandExecutor
from apps.compositor.worker_manager.redis_ipc import (
    RedisClientProtocol,
    clear_session_ready,
    mark_session_ready,
    session_commands_key,
    session_reply_key,
)
from apps.compositor.session_producer_poller import stop_session_producer_poller
from apps.compositor.worker_manager.producer_poll import attach_producer_poller
from apps.compositor.worker_manager.worker_heartbeat import SessionWorkerHeartbeat
from apps.sessions.models import StudioSession

logger = logging.getLogger(__name__)


def run_session_worker(
    session_id: str,
    *,
    redis_client: RedisClientProtocol,
) -> int:
    """
    Bootstrap one session on this process and consume Redis command messages.

    Returns a process exit code (0 = clean shutdown).
    """
    session = StudioSession.objects.select_related('active_scene').get(pk=session_id)
    executor = SessionCommandExecutor(session_id)
    heartbeat: SessionWorkerHeartbeat | None = None

    def bootstrap() -> SessionIngestManager:
        return SessionIngestManager.create(session)

    executor.start()
    try:
        ingest_manager = executor.submit_callable('bootstrap', bootstrap)
        executor.bind_ingest_manager(ingest_manager)
    except Exception:
        logger.exception('Failed to bootstrap session worker for %s', session_id)
        executor.shutdown()
        return 1

    mark_session_ready(redis_client, session_id)
    attach_producer_poller(session_id, session_id, executor)
    heartbeat = SessionWorkerHeartbeat(session_id, redis_client=redis_client)
    heartbeat.start()
    logger.info('Session worker ready for %s', session_id)

    commands_key = session_commands_key(session_id)
    exit_code = 0
    try:
        while True:
            item = redis_client.blpop([commands_key], timeout=1)
            if item is None:
                continue

            payload = json.loads(item[1])
            action = payload.get('action')
            if action == 'shutdown':
                logger.info('Session worker shutting down for %s', session_id)
                break

            if action != 'command':
                logger.warning(
                    'Ignoring unknown worker message for %s: %s',
                    session_id,
                    payload,
                )
                continue

            command = decode_command(payload['command'])
            result = _execute_command(executor, command)
            _publish_reply(redis_client, session_id, result)
    except KeyboardInterrupt:
        logger.info('Session worker interrupted for %s', session_id)
    except Exception:
        logger.exception('Session worker crashed for %s', session_id)
        exit_code = 1
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        stop_session_producer_poller(session_id)
        try:
            executor.submit_callable('stop', ingest_manager.stop)
        except Exception:
            logger.exception('Failed to stop ingest manager for %s', session_id)
        executor.shutdown()
        clear_session_ready(redis_client, session_id)

    return exit_code


def _execute_command(
    executor: SessionCommandExecutor,
    command: SessionCommand,
) -> CommandResult:
    try:
        return executor.submit_command(command)
    except Exception as exc:
        logger.exception(
            'Command %s failed in worker for session %s',
            command.command_type.value,
            command.session_id,
        )
        return CommandResult.fail(command.command_id, str(exc))


def _publish_reply(
    redis_client: RedisClientProtocol,
    session_id: str,
    result: CommandResult,
) -> None:
    payload = encode_result(result)
    redis_client.set(
        session_reply_key(session_id, result.command_id),
        json.dumps(payload),
        ex=int(getattr(settings, 'SESSION_COMMAND_TIMEOUT_SEC', 30)) + 5,
    )
