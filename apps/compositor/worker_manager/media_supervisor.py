"""Media Supervisor — spawns and monitors session worker subprocesses."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.compositor.worker_manager.redis_ipc import (
    RedisClientProtocol,
    SUPERVISOR_CONTROL_KEY,
    clear_session_ready,
    get_redis_client,
)
from apps.compositor.worker_manager.session_affinity import (
    get_session_affinity,
    increment_restart_count,
    is_session_expected,
    mark_session_unexpected,
)
from apps.compositor.worker_manager.worker_heartbeat import is_session_heartbeat_alive

logger = logging.getLogger(__name__)


@dataclass
class WorkerProcess:
    session_id: str
    process: subprocess.Popen[bytes]


class MediaSupervisor:
    """Consumes supervisor control messages and manages worker subprocesses."""

    def __init__(
        self,
        *,
        redis_client: RedisClientProtocol | None = None,
        manage_py: Path | None = None,
    ) -> None:
        self._redis = redis_client or get_redis_client()
        self._manage_py = manage_py or _default_manage_py()
        self._workers: dict[str, WorkerProcess] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor_interval = float(
            getattr(settings, 'SESSION_WORKER_MONITOR_INTERVAL_SEC', 2)
        )
        self._max_restarts = int(getattr(settings, 'SESSION_WORKER_MAX_RESTARTS', 3))
        self._restart_cooldown = float(
            getattr(settings, 'SESSION_WORKER_RESTART_COOLDOWN_SEC', 2)
        )

    def run_forever(self) -> None:
        logger.info('Media Supervisor started (manage.py=%s)', self._manage_py)
        monitor = threading.Thread(
            target=self._monitor_workers,
            name='media-supervisor-monitor',
            daemon=True,
        )
        monitor.start()

        while not self._stop_event.is_set():
            item = self._redis.blpop([SUPERVISOR_CONTROL_KEY], timeout=1)
            if item is None:
                continue
            try:
                payload = json.loads(item[1])
            except json.JSONDecodeError:
                logger.warning('Ignoring invalid supervisor control payload: %s', item[1])
                continue
            self._handle_control(payload)

        self.shutdown_all()

    def shutdown_all(self) -> None:
        with self._lock:
            session_ids = list(self._workers.keys())
        for session_id in session_ids:
            self._destroy_worker(session_id, graceful=True)

    def _handle_control(self, payload: dict) -> None:
        action = payload.get('action')
        session_id = payload.get('session_id')
        if not session_id:
            logger.warning('Supervisor control message missing session_id: %s', payload)
            return

        if action == 'spawn':
            self._spawn_worker(session_id)
            return

        if action == 'destroy':
            self._destroy_worker(session_id, graceful=payload.get('graceful', True))
            return

        logger.warning('Unknown supervisor control action: %s', action)

    def _spawn_worker(self, session_id: str) -> None:
        with self._lock:
            existing = self._workers.get(session_id)
            if existing is not None and existing.process.poll() is None:
                logger.info('Worker already running for session %s', session_id)
                return

        affinity = get_session_affinity(session_id, redis_client=self._redis)
        env = os.environ.copy()
        if affinity is not None:
            env['COMPOSITOR_CUDA_DEVICE_ID'] = str(affinity.cuda_device_id)
            env['COMPOSITOR_RTP_PORT_MIN'] = str(affinity.rtp_port_min)
            env['COMPOSITOR_RTP_PORT_MAX'] = str(affinity.rtp_port_max)

        command = [
            sys.executable,
            str(self._manage_py),
            'run_session_worker',
            session_id,
        ]
        logger.info('Spawning session worker for %s: %s', session_id, ' '.join(command))
        process = subprocess.Popen(
            command,
            cwd=str(self._manage_py.parent),
            env=env,
        )
        with self._lock:
            self._workers[session_id] = WorkerProcess(session_id=session_id, process=process)

    def _destroy_worker(self, session_id: str, *, graceful: bool) -> None:
        with self._lock:
            worker = self._workers.pop(session_id, None)
        if worker is None:
            return

        if graceful:
            self._send_worker_shutdown(session_id)

        timeout = float(getattr(settings, 'GRACEFUL_SHUTDOWN_TIMEOUT_SEC', 30))
        try:
            worker.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                'Force-killing session worker for %s after %ss',
                session_id,
                timeout,
            )
            worker.process.kill()
            worker.process.wait(timeout=5)

        clear_session_ready(self._redis, session_id)
        logger.info('Session worker stopped for %s', session_id)

    def _send_worker_shutdown(self, session_id: str) -> None:
        from apps.compositor.worker_manager.redis_ipc import enqueue_session_command

        enqueue_session_command(
            self._redis,
            session_id,
            {'action': 'shutdown'},
        )

    def _monitor_workers(self) -> None:
        while not self._stop_event.wait(self._monitor_interval):
            with self._lock:
                workers = list(self._workers.items())
            for session_id, worker in workers:
                exit_code = worker.process.poll()
                if exit_code is not None:
                    self._handle_worker_failure(
                        session_id,
                        reason=f'process exited with code {exit_code}',
                    )
                    continue
                if not is_session_heartbeat_alive(session_id, redis_client=self._redis):
                    logger.warning('Stale heartbeat for session %s', session_id)
                    self._force_kill_worker(session_id)
                    self._handle_worker_failure(
                        session_id,
                        reason='heartbeat timeout',
                    )

    def _force_kill_worker(self, session_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(session_id, None)
        if worker is None:
            return
        if worker.process.poll() is None:
            worker.process.kill()
            worker.process.wait(timeout=5)

    def _handle_worker_failure(self, session_id: str, *, reason: str) -> None:
        with self._lock:
            self._workers.pop(session_id, None)

        if not is_session_expected(session_id, redis_client=self._redis):
            clear_session_ready(self._redis, session_id)
            return

        affinity = get_session_affinity(session_id, redis_client=self._redis)
        if affinity is None:
            return

        if affinity.restart_count >= self._max_restarts:
            logger.error(
                'Session worker for %s exceeded max restarts (%s); giving up (%s)',
                session_id,
                self._max_restarts,
                reason,
            )
            mark_session_unexpected(session_id, redis_client=self._redis)
            clear_session_ready(self._redis, session_id)
            return

        updated = increment_restart_count(session_id, redis_client=self._redis)
        logger.warning(
            'Restarting session worker for %s (attempt %s/%s) after %s',
            session_id,
            updated.restart_count if updated else '?',
            self._max_restarts,
            reason,
        )
        time.sleep(self._restart_cooldown)
        if is_session_expected(session_id, redis_client=self._redis):
            self._spawn_worker(session_id)


def _default_manage_py() -> Path:
    return Path(settings.BASE_DIR) / 'manage.py'
