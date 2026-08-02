"""Session-scoped Twitch chat ingestion into the compositor overlay."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from apps.graphics.service import GraphicsService
from apps.integrations.twitch_chat.credentials import fetch_twitch_chat_credentials
from apps.integrations.twitch_chat.irc_listener import TwitchIrcListener

logger = logging.getLogger(__name__)

MAX_MESSAGES = 20
FLUSH_INTERVAL_SEC = 1.5


@dataclass
class _SessionChatState:
    session_id: uuid.UUID
    tenant_id: str
    messages: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=MAX_MESSAGES))
    dirty: bool = False
    listener: TwitchIrcListener | None = None
    loop: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    flush_task: asyncio.Task | None = None


class TwitchChatManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionChatState] = {}
        self._graphics = GraphicsService()

    def start(self, session_id: uuid.UUID, tenant_id: str) -> None:
        key = str(session_id)
        with self._lock:
            if key in self._sessions:
                return

        credentials = fetch_twitch_chat_credentials(tenant_id)
        if credentials is None:
            logger.info('No Twitch chat credentials for tenant %s; skipping IRC', tenant_id)
            return

        state = _SessionChatState(session_id=session_id, tenant_id=tenant_id)

        def on_message(author: str, text: str) -> None:
            with self._lock:
                active = self._sessions.get(key)
                if active is None:
                    return
                active.messages.append({'author': author, 'text': text})
                active.dirty = True

        listener = TwitchIrcListener(
            access_token=credentials['access_token'],
            nick=credentials['nick'],
            channel=credentials['channel'],
            on_message=on_message,
        )
        state.listener = listener

        thread = threading.Thread(
            target=self._run_session_loop,
            args=(key, state, listener),
            name=f'twitch-chat-{key}',
            daemon=True,
        )
        state.thread = thread

        with self._lock:
            self._sessions[key] = state

        thread.start()
        logger.info('Started Twitch chat listener for session %s', session_id)

    def stop(self, session_id: uuid.UUID) -> None:
        key = str(session_id)
        with self._lock:
            state = self._sessions.pop(key, None)
        if state is None:
            return

        if state.listener is not None:
            state.listener.request_stop()
        if state.loop is not None and state.flush_task is not None:
            state.loop.call_soon_threadsafe(state.flush_task.cancel)

        if state.thread is not None:
            state.thread.join(timeout=5)

        try:
            self._graphics.update_chat(
                session_id,
                {'enabled': False, 'messages': []},
            )
        except Exception:
            logger.exception('Failed to clear chat overlay for session %s', session_id)

    def _run_session_loop(
        self,
        key: str,
        state: _SessionChatState,
        listener: TwitchIrcListener,
    ) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state.loop = loop

        async def flush_loop() -> None:
            while True:
                await asyncio.sleep(FLUSH_INTERVAL_SEC)
                with self._lock:
                    active = self._sessions.get(key)
                    if active is None:
                        return
                    if not active.dirty:
                        continue
                    messages: list[dict[str, Any]] = list(active.messages)
                    active.dirty = False

                try:
                    self._graphics.update_chat(
                        state.session_id,
                        {'enabled': True, 'messages': messages},
                    )
                except Exception:
                    logger.exception(
                        'Failed to push chat overlay for session %s',
                        state.session_id,
                    )

        async def main() -> None:
            flush_task = asyncio.create_task(flush_loop())
            state.flush_task = flush_task
            try:
                await listener.run()
            finally:
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

        try:
            loop.run_until_complete(main())
        finally:
            loop.close()


_manager: TwitchChatManager | None = None


def get_twitch_chat_manager() -> TwitchChatManager:
    global _manager
    if _manager is None:
        _manager = TwitchChatManager()
    return _manager
