from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.recording.exceptions import (
    IngestManagerNotRunningError,
    RecordingAlreadyActiveError,
    RecordingNotActiveError,
)
from apps.recording.serializers import RecordingSerializer
from apps.recording.service import RecordingService
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError


def _recording_service() -> RecordingService:
    return RecordingService()


class SessionRecordingListView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            recordings = _recording_service().list_recordings(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            RecordingSerializer(
                [
                    {
                        'recording_id': recording.recording_id,
                        'session_id': recording.session_id,
                        'status': recording.status,
                        'file_path': recording.file_path,
                        'started_at': recording.started_at,
                        'stopped_at': recording.stopped_at,
                    }
                    for recording in recordings
                ],
                many=True,
            ).data
        )


class SessionRecordingStartView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        try:
            recording = _recording_service().start_recording(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except RecordingAlreadyActiveError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except IngestManagerNotRunningError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(
            RecordingSerializer(
                {
                    'recording_id': recording.recording_id,
                    'session_id': recording.session_id,
                    'status': recording.status,
                    'file_path': recording.file_path,
                    'started_at': recording.started_at,
                    'stopped_at': recording.stopped_at,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class SessionRecordingStopView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        try:
            recording = _recording_service().stop_recording(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except RecordingNotActiveError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except IngestManagerNotRunningError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(
            RecordingSerializer(
                {
                    'recording_id': recording.recording_id,
                    'session_id': recording.session_id,
                    'status': recording.status,
                    'file_path': recording.file_path,
                    'started_at': recording.started_at,
                    'stopped_at': recording.stopped_at,
                }
            ).data
        )
