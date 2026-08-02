"""Twitch IRC WebSocket listener for live chat messages."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

import aiohttp

logger = logging.getLogger(__name__)

IRC_WS_URL = 'wss://irc-ws.chat.twitch.tv:443'
PRIVMSG_RE = re.compile(r'(?:@[^ ]+ )?:([^!]+)!.*? PRIVMSG #[^ ]+ :(.*)')


def parse_privmsg(line: str) -> tuple[str, str] | None:
    match = PRIVMSG_RE.match(line)
    if match is None:
        return None
    return match.group(1), match.group(2)


class TwitchIrcListener:
    def __init__(
        self,
        *,
        access_token: str,
        nick: str,
        channel: str,
        on_message: Callable[[str, str], None],
    ) -> None:
        self._token = access_token
        self._nick = nick
        self._channel = channel.lower().lstrip('#')
        self._on_message = on_message
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception('Twitch IRC connection error; reconnecting in 5s')
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5.0)
                except TimeoutError:
                    pass

    async def _connect_and_listen(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(IRC_WS_URL, heartbeat=30) as ws:
                await ws.send_str(f'PASS oauth:{self._token}')
                await ws.send_str(f'NICK {self._nick}')
                await ws.send_str(f'JOIN #{self._channel}')
                logger.info('Joined Twitch IRC channel #%s as %s', self._channel, self._nick)

                while not self._stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    except TimeoutError:
                        continue

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        for line in msg.data.split('\r\n'):
                            if not line:
                                continue
                            if line.startswith('PING'):
                                await ws.send_str(f'PONG {line.split(" ", 1)[1]}')
                                continue
                            parsed = parse_privmsg(line)
                            if parsed is not None:
                                author, text = parsed
                                self._on_message(author, text)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
