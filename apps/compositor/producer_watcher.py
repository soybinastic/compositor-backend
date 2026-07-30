"""Deprecated: use session_producer_poller instead."""

from apps.compositor.session_producer_poller import (
    SessionProducerPoller,
    get_poller_registry,
)

__all__ = ['SessionProducerPoller', 'get_poller_registry']
