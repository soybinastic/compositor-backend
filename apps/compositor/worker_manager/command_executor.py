"""Per-session FIFO command executor with a GLib main loop."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent import futures
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version('GLib', '2.0')
from django.conf import settings
from gi.repository import GLib  # noqa: E402

from apps.compositor.commands import CommandResult, SessionCommand
from apps.compositor.session_ingest_manager import SessionIngestManager
from apps.compositor.worker_manager.command_dispatch import dispatch_command

logger = logging.getLogger(__name__)


@dataclass
class _WorkItem:
    label: str
    run: Callable[[], Any]
    future: futures.Future[Any]


class SessionCommandExecutor:
    """
    Serializes session media mutations on a dedicated GLib main-loop thread.

    API and background pollers enqueue work; GLib idle handlers drain the
    queue so pipeline mutations and bus watches share one thread context.
    """

    def __init__(
        self,
        session_id: str,
        ingest_manager: SessionIngestManager | None = None,
        *,
        command_timeout_sec: float | None = None,
    ) -> None:
        self.session_id = session_id
        self._ingest_manager = ingest_manager
        self._command_timeout_sec = (
            command_timeout_sec
            if command_timeout_sec is not None
            else float(getattr(settings, 'SESSION_COMMAND_TIMEOUT_SEC', 30))
        )
        self._queue: queue.Queue[_WorkItem | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name=f'session-command-{session_id}',
            daemon=True,
        )
        self._main_context: GLib.MainContext | None = None
        self._main_loop: GLib.MainLoop | None = None
        self._loop_ready = threading.Event()
        self._started = False
        self._closed = False

    @property
    def ingest_manager(self) -> SessionIngestManager | None:
        return self._ingest_manager

    @property
    def thread_id(self) -> int | None:
        if not self._started:
            return None
        return self._thread.ident

    def bind_ingest_manager(self, ingest_manager: SessionIngestManager) -> None:
        self._ingest_manager = ingest_manager

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        if not self._loop_ready.wait(timeout=self._command_timeout_sec):
            raise RuntimeError(
                f'GLib main loop failed to start for session {self.session_id}'
            )
        logger.debug(
            'Started GLib command executor for session %s',
            self.session_id,
        )

    def submit_command(self, command: SessionCommand) -> CommandResult:
        ingest_manager = self._require_ingest_manager()

        def _run() -> CommandResult:
            data = dispatch_command(ingest_manager, command)
            return CommandResult.ok(command.command_id, data=data)

        future: futures.Future[CommandResult] = futures.Future()
        self._enqueue(
            f'command:{command.command_type.value}',
            _run,
            future,
        )
        return self._wait(future)

    def submit_callable(self, label: str, fn: Callable[[], Any]) -> Any:
        future: futures.Future[Any] = futures.Future()
        self._enqueue(label, fn, future)
        return self._wait(future)

    def shutdown(self, *, timeout_sec: float | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._schedule_drain()
        timeout = timeout_sec if timeout_sec is not None else self._command_timeout_sec
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning(
                'Command executor for session %s did not stop within %ss',
                self.session_id,
                timeout,
            )

    def _require_ingest_manager(self) -> SessionIngestManager:
        if self._ingest_manager is None:
            raise RuntimeError(
                f'Command executor for session {self.session_id} '
                'has no ingest manager bound'
            )
        return self._ingest_manager

    def _enqueue(
        self,
        label: str,
        run: Callable[[], Any],
        future: futures.Future[Any],
    ) -> None:
        if self._closed:
            future.set_exception(
                RuntimeError(
                    f'Command executor for session {self.session_id} is closed'
                )
            )
            return
        self._queue.put(_WorkItem(label=label, run=run, future=future))
        self._schedule_drain()

    def _wait(self, future: futures.Future[Any]) -> Any:
        try:
            return future.result(timeout=self._command_timeout_sec)
        except futures.TimeoutError as exc:
            raise TimeoutError(
                f'Command timed out after {self._command_timeout_sec}s '
                f'for session {self.session_id}'
            ) from exc

    def _run(self) -> None:
        context = GLib.MainContext.new()
        context.push_thread_default()
        self._main_context = context
        self._main_loop = GLib.MainLoop(context)
        self._loop_ready.set()
        GLib.idle_add(self._drain_idle)
        try:
            self._main_loop.run()
        finally:
            context.pop_thread_default()
        logger.debug(
            'GLib main loop exited for session %s',
            self.session_id,
        )

    def _schedule_drain(self) -> None:
        if threading.current_thread() is self._thread:
            self._drain_queue()
            return
        if self._main_context is None:
            return
        self._main_context.invoke_full(
            GLib.PRIORITY_DEFAULT,
            self._drain_idle,
            None,
        )

    def _drain_idle(self, _user_data: object | None = None) -> bool:
        self._drain_queue()
        return False

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                if item is None:
                    if self._main_loop is not None:
                        self._main_loop.quit()
                    return
                self._execute(item)
            finally:
                self._queue.task_done()

    def _execute(self, item: _WorkItem) -> None:
        if item.future.done():
            return
        try:
            item.future.set_result(item.run())
        except Exception as exc:
            logger.exception(
                'Executor work %s failed for session %s',
                item.label,
                self.session_id,
            )
            item.future.set_exception(exc)


class SessionCommandExecutorRegistry:
    """Tracks per-session command executors for the in-process runtime."""

    def __init__(self) -> None:
        self._executors: dict[str, SessionCommandExecutor] = {}
        self._lock = threading.Lock()

    def attach(
        self,
        session_id: str,
        ingest_manager_or_bootstrap: (
            SessionIngestManager | Callable[[], SessionIngestManager]
        ),
    ) -> SessionCommandExecutor:
        if callable(ingest_manager_or_bootstrap):
            executor = SessionCommandExecutor(session_id)
            with self._lock:
                self._executors[session_id] = executor
            executor.start()
            manager = executor.submit_callable(
                'bootstrap',
                ingest_manager_or_bootstrap,
            )
            executor.bind_ingest_manager(manager)
            return executor

        executor = SessionCommandExecutor(session_id, ingest_manager_or_bootstrap)
        with self._lock:
            self._executors[session_id] = executor
        executor.start()
        return executor

    def get(self, session_id: str) -> SessionCommandExecutor | None:
        with self._lock:
            return self._executors.get(session_id)

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._executors.keys())

    def pop(self, session_id: str) -> SessionCommandExecutor | None:
        with self._lock:
            return self._executors.pop(session_id, None)

    def shutdown_all(self, *, timeout_sec: float | None = None) -> None:
        with self._lock:
            executors = list(self._executors.values())
            self._executors.clear()
        for executor in executors:
            executor.shutdown(timeout_sec=timeout_sec)


_executor_registry = SessionCommandExecutorRegistry()


def get_executor_registry() -> SessionCommandExecutorRegistry:
    return _executor_registry
