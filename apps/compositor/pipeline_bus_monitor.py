"""GStreamer pipeline bus monitor for streaming error detection."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)


class PipelineBusMonitor:
    """Watches a GStreamer pipeline bus for errors on specific elements."""

    def __init__(
        self,
        pipeline: Gst.Pipeline,
        *,
        watched_elements: set[Gst.Element],
        on_error: Callable[[str], None],
    ) -> None:
        self._pipeline = pipeline
        self._watched_elements = watched_elements
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name='pipeline-bus-monitor',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        bus = self._pipeline.get_bus()
        while not self._stop_event.is_set():
            message = bus.timed_pop(200 * Gst.MSECOND)
            if message is None:
                continue

            if message.type == Gst.MessageType.ERROR:
                source = message.src
                if source in self._watched_elements:
                    err, debug = message.parse_error()
                    self._on_error(f'{err} ({debug})')
                    return

            if message.type == Gst.MessageType.EOS and self._stop_event.is_set():
                break
