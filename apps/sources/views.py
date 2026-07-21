from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sources.exceptions import (
    IngestManagerNotRunningError,
    InvalidRtmpUrlError,
    RtmpSourceNotFoundError,
)
from apps.sources.serializers import AddRtmpSourceSerializer, RtmpSourceSerializer
from apps.sources.service import RtmpSourceService


def _rtmp_source_service() -> RtmpSourceService:
    return RtmpSourceService()


def _serialize_source(source) -> dict:
    return {
        'source_id': source.source_id,
        'session_id': source.session_id,
        'url': source.url,
        'display_name': source.display_name,
        'status': source.status,
        'started_at': source.started_at,
        'stopped_at': source.stopped_at,
        'video_buffers': source.video_buffers,
        'audio_buffers': source.audio_buffers,
    }


class SessionRtmpSourceListView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            sources = _rtmp_source_service().list_sources(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            RtmpSourceSerializer(
                [_serialize_source(source) for source in sources],
                many=True,
            ).data
        )


class SessionRtmpSourceCreateView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        serializer = AddRtmpSourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            source = _rtmp_source_service().add_source(
                session_id,
                url=serializer.validated_data['url'],
                display_name=serializer.validated_data.get('display_name', ''),
            )
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except InvalidRtmpUrlError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IngestManagerNotRunningError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(
            RtmpSourceSerializer(_serialize_source(source)).data,
            status=status.HTTP_201_CREATED,
        )


class SessionRtmpSourceDeleteView(APIView):
    authentication_classes = []
    permission_classes = []

    def delete(self, request, session_id, source_id):
        try:
            source = _rtmp_source_service().remove_source(session_id, source_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except RtmpSourceNotFoundError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)

        return Response(RtmpSourceSerializer(_serialize_source(source)).data)
