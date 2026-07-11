"""Async webhook delivery for studio lifecycle events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_worker: threading.Thread | None = None
_queue: list[dict[str, Any]] = []
_stop_event = threading.Event()


def emit_event(event_type: str, payload: dict[str, Any]) -> None:
    """Queue a webhook event for delivery. No-op when WEBHOOK_URL is unset."""
    webhook_url = getattr(settings, 'WEBHOOK_URL', '')
    if not webhook_url:
        return

    body = {
        'event': event_type,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'payload': payload,
    }

    with _lock:
        _queue.append(body)
        _ensure_worker()

    logger.debug('Queued webhook event %s', event_type)


def _ensure_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return

    _stop_event.clear()
    _worker = threading.Thread(
        target=_run_worker,
        name='webhook-dispatcher',
        daemon=True,
    )
    _worker.start()


def _run_worker() -> None:
    while not _stop_event.is_set():
        with _lock:
            if not _queue:
                break
            body = _queue.pop(0)

        try:
            _deliver(body)
        except Exception:
            logger.exception('Webhook delivery failed for event %s', body.get('event'))

    with _lock:
        global _worker
        _worker = None


def _deliver(body: dict[str, Any]) -> None:
    webhook_url = settings.WEBHOOK_URL
    data = json.dumps(body).encode('utf-8')
    headers = {'Content-Type': 'application/json'}

    secret = getattr(settings, 'WEBHOOK_SECRET', '')
    if secret:
        signature = hmac.new(secret.encode('utf-8'), data, hashlib.sha256).hexdigest()
        headers['X-Studio-Signature'] = f'sha256={signature}'

    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers=headers,
        method='POST',
    )
    timeout = getattr(settings, 'WEBHOOK_TIMEOUT_SEC', 5)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                logger.warning(
                    'Webhook returned HTTP %s for event %s',
                    response.status,
                    body.get('event'),
                )
    except urllib.error.URLError as exc:
        logger.warning('Webhook delivery error for %s: %s', body.get('event'), exc.reason)


def flush_pending(timeout_sec: float = 2.0) -> None:
    """Wait for queued webhook events to drain (used during shutdown)."""
    deadline = datetime.now(timezone.utc).timestamp() + timeout_sec
    while datetime.now(timezone.utc).timestamp() < deadline:
        with _lock:
            if not _queue and (_worker is None or not _worker.is_alive()):
                return
        threading.Event().wait(0.05)


def stop_worker() -> None:
    """Signal the webhook worker to exit after draining."""
    _stop_event.set()
    if _worker and _worker.is_alive():
        _worker.join(timeout=getattr(settings, 'WEBHOOK_TIMEOUT_SEC', 5) + 1)
