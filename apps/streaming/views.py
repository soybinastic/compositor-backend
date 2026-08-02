from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.streaming.exceptions import (
    IngestManagerNotRunningError,
    InvalidDestinationError,
    StreamAlreadyActiveError,
    StreamNotActiveError,
)
from apps.streaming.serializers import StartStreamSerializer, StreamSerializer
from apps.streaming.service import StreamingService


def _streaming_service() -> StreamingService:
    return StreamingService()


def _serialize_stream(stream) -> dict:
    return {
        'stream_id': stream.stream_id,
        'session_id': stream.session_id,
        'destination_type': stream.destination_type,
        'destination_url': stream.destination_url,
        'destination_urls': stream.destination_urls,
        'destinations': [
            {
                'destination_id': destination.destination_id,
                'url': destination.url,
                'label': destination.label,
                'status': destination.status,
                'started_at': destination.started_at,
                'stopped_at': destination.stopped_at,
            }
            for destination in stream.destinations
        ],
        'output_path': stream.output_path,
        'status': stream.status,
        'started_at': stream.started_at,
        'stopped_at': stream.stopped_at,
    }


class SessionStreamListView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            streams = _streaming_service().list_streams(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            StreamSerializer([_serialize_stream(stream) for stream in streams], many=True).data
        )


class SessionStreamStartView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        serializer = StartStreamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        rtmp_entries = validated.get('_rtmp_entries', [])

        try:
            stream = _streaming_service().start_stream(
                session_id,
                destination_type=validated['destination_type'],
                destination_url=validated.get('destination_url', ''),
                destination_urls=[url for url, _label in rtmp_entries] or None,
                destinations=[
                    {'url': url, 'label': label}
                    for url, label in rtmp_entries
                ]
                if rtmp_entries
                else None,
                tenant_id=str(validated['tenant_id']) if validated.get('tenant_id') else None,
                twitch_chat_enabled=validated.get('twitch_chat_enabled', False),
            )
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except StreamAlreadyActiveError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except InvalidDestinationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IngestManagerNotRunningError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(
            StreamSerializer(_serialize_stream(stream)).data,
            status=status.HTTP_201_CREATED,
        )


class SessionStreamStopView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        try:
            stream = _streaming_service().stop_stream(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except StreamNotActiveError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except IngestManagerNotRunningError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(StreamSerializer(_serialize_stream(stream)).data)
