import json
import os
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.compositor.worker_manager.worker_event_consumer import WorkerEventConsumer
from apps.compositor.worker_manager.worker_event_ipc import WORKER_EVENTS_KEY
from apps.streaming.models import DestinationType, SessionStream, StreamStatus
from core import events
from core.worker_event_dispatch import dispatch_worker_event
from core.worker_events import WORKER_PROCESS_ENV, emit_worker_event, mark_worker_process


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def blpop(self, keys: list[str], timeout: int = 0):
        del timeout
        for key in keys:
            items = self.lists.get(key, [])
            if items:
                return key, items.pop(0)
        return None


class WorkerEventDispatchTests(TestCase):
    @patch('core.worker_event_dispatch.emit_event')
    @patch('apps.streaming.service.StreamingService')
    def test_stream_failed_marks_orm_and_emits_webhook(
        self,
        mock_streaming_cls,
        mock_emit_event,
    ):
        session_id = str(uuid.uuid4())
        dispatch_worker_event(
            events.STREAM_FAILED,
            {'session_id': session_id, 'error': 'connection lost'},
        )

        mock_emit_event.assert_called_once()
        mock_streaming_cls.return_value.mark_active_stream_failed.assert_called_once()

    @patch('core.worker_event_dispatch.emit_event')
    @patch('apps.streaming.service.StreamingService')
    def test_stream_destination_failed_marks_single_destination(
        self,
        mock_streaming_cls,
        mock_emit_event,
    ):
        session_id = str(uuid.uuid4())
        dispatch_worker_event(
            events.STREAM_DESTINATION_FAILED,
            {
                'session_id': session_id,
                'destination_url': 'rtmp://live.twitch.tv/app/key',
                'error': 'connection lost',
            },
        )

        mock_emit_event.assert_called_once()
        mock_streaming_cls.return_value.mark_stream_destination_failed.assert_called_once()


class WorkerEventEmitterTests(TestCase):
    def tearDown(self):
        os.environ.pop(WORKER_PROCESS_ENV, None)

    @patch('core.worker_event_dispatch.dispatch_worker_event')
    def test_emit_worker_event_dispatches_locally_in_api_process(self, mock_dispatch):
        os.environ.pop(WORKER_PROCESS_ENV, None)
        emit_worker_event(events.STREAM_FAILED, 'session-1', {'error': 'boom'})
        mock_dispatch.assert_called_once()

    @patch('apps.compositor.worker_manager.worker_event_ipc.publish_worker_event')
    def test_emit_worker_event_publishes_in_worker_subprocess(self, mock_publish):
        mark_worker_process()
        emit_worker_event(events.STREAM_FAILED, 'session-1', {'error': 'boom'})
        mock_publish.assert_called_once()


class WorkerEventConsumerTests(TestCase):
    @patch('apps.compositor.worker_manager.worker_event_consumer.dispatch_worker_event')
    def test_consumer_drains_redis_queue(self, mock_dispatch):
        redis = FakeRedis()
        redis.rpush(
            WORKER_EVENTS_KEY,
            json.dumps(
                {
                    'event': events.STREAM_RECONNECTED,
                    'payload': {'session_id': 'session-1', 'attempt': 2},
                }
            ),
        )
        consumer = WorkerEventConsumer(redis_client=redis)
        consumer._handle_message(redis.lists[WORKER_EVENTS_KEY][0])

        mock_dispatch.assert_called_once_with(
            events.STREAM_RECONNECTED,
            {'session_id': 'session-1', 'attempt': 2},
        )


class WorkerEventIntegrationTests(TestCase):
    @override_settings(WEBHOOK_URL='')
    def test_stream_failed_updates_live_stream_record(self):
        session_id = uuid.uuid4()
        from apps.sessions.models import SessionStatus, StudioSession

        session = StudioSession.objects.create(
            id=session_id,
            host_display_name='Host',
            status=SessionStatus.ACTIVE,
        )
        stream = SessionStream.objects.create(
            session=session,
            destination_type=DestinationType.RTMP,
            destination_url='rtmp://example/live',
            status=StreamStatus.LIVE,
        )

        dispatch_worker_event(
            events.STREAM_FAILED,
            {'session_id': str(session_id), 'error': 'encoder error'},
        )

        stream.refresh_from_db()
        self.assertEqual(stream.status, StreamStatus.FAILED)
