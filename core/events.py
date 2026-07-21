"""Studio lifecycle event types for webhooks and internal dispatch."""

from __future__ import annotations

SESSION_CREATED = 'session.created'
SESSION_ENDED = 'session.ended'

RECORDING_STARTED = 'recording.started'
RECORDING_STOPPED = 'recording.stopped'
RECORDING_FAILED = 'recording.failed'

STREAM_STARTED = 'stream.started'
STREAM_STOPPED = 'stream.stopped'
STREAM_FAILED = 'stream.failed'
STREAM_RECONNECTING = 'stream.reconnecting'
STREAM_RECONNECTED = 'stream.reconnected'

RTMP_SOURCE_STARTED = 'rtmp_source.started'
RTMP_SOURCE_STOPPED = 'rtmp_source.stopped'
RTMP_SOURCE_FAILED = 'rtmp_source.failed'
