"""Fetch Twitch IRC credentials from studio-persistence."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def fetch_twitch_chat_credentials(tenant_id: str) -> dict[str, Any] | None:
    base_url = getattr(settings, 'PERSISTENCE_API_URL', '').rstrip('/')
    if not base_url:
        logger.warning('PERSISTENCE_API_URL is not configured; skipping Twitch chat')
        return None

    url = f'{base_url}/tenant/{tenant_id}/integrations/twitch/chat-credentials/'
    request = urllib.request.Request(url, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        logger.warning('Persistence chat-credentials request failed: HTTP %s', exc.code)
        return None
    except Exception:
        logger.exception('Failed to fetch Twitch chat credentials for tenant %s', tenant_id)
        return None

    nick = str(payload.get('nick', '')).strip()
    channel = str(payload.get('channel', '')).strip()
    access_token = str(payload.get('access_token', '')).strip()
    if not nick or not channel or not access_token:
        logger.warning('Incomplete Twitch chat credentials for tenant %s', tenant_id)
        return None

    return {
        'nick': nick,
        'channel': channel,
        'access_token': access_token,
    }
