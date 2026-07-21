"""Thread-safe HTTP asset cache for graphic URLs."""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 32
_DEFAULT_TTL_SEC = 600.0
_DEFAULT_TIMEOUT_SEC = 15.0


class AssetCache:
    """LRU cache of URL → bytes with TTL."""

    def __init__(
        self,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl_sec: float = _DEFAULT_TTL_SEC,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_sec = ttl_sec
        self._timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[bytes, float]] = OrderedDict()

    def get(self, url: str) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            item = self._entries.get(url)
            if item is None:
                return None
            data, expires_at = item
            if expires_at < now:
                del self._entries[url]
                return None
            self._entries.move_to_end(url)
            return data

    def put(self, url: str, data: bytes) -> None:
        with self._lock:
            self._entries[url] = (data, time.monotonic() + self._ttl_sec)
            self._entries.move_to_end(url)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def fetch(self, url: str) -> bytes:
        cached = self.get(url)
        if cached is not None:
            return cached

        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'mini-streaming-studio-compositor/1.0'},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as response:
                data = response.read()
        except urllib.error.URLError as exc:
            logger.warning('Failed to download graphic asset url=%s: %s', url, exc)
            raise

        self.put(url, data)
        return data

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_global_cache = AssetCache()


def get_asset_cache() -> AssetCache:
    return _global_cache
