from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.background_music.exceptions import (
    IngestManagerNotRunningError,
    SceneNotActiveError,
)
from apps.background_music.serializers import (
    BackgroundMusicCommandAckSerializer,
    BackgroundMusicRuntimeStateSerializer,
    BackgroundMusicVolumeSerializer,
)
from apps.background_music.service import BackgroundMusicService
from apps.scenes.exceptions import SceneNotFoundError
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError

_TRANSPORT_ACTIONS = frozenset({'play', 'pause', 'resume', 'stop', 'volume'})


def _background_music_service() -> BackgroundMusicService:
    return BackgroundMusicService()


class SessionBackgroundMusicStateView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            state = _background_music_service().get_runtime_state(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(BackgroundMusicRuntimeStateSerializer(state).data)


class SessionSceneBackgroundMusicTransportView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, scene_id, action):
        if action not in _TRANSPORT_ACTIONS:
            return Response(
                {'detail': f'Unknown transport action: {action}'},
                status=status.HTTP_404_NOT_FOUND,
            )

        service = _background_music_service()

        try:
            if action == 'play':
                ack = service.play(session_id, scene_id)
            elif action == 'pause':
                ack = service.pause(session_id, scene_id)
            elif action == 'resume':
                ack = service.resume(session_id, scene_id)
            elif action == 'stop':
                ack = service.stop(session_id, scene_id)
            else:
                serializer = BackgroundMusicVolumeSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                ack = service.set_volume(
                    session_id,
                    scene_id,
                    volume=serializer.validated_data['volume'],
                    muted=serializer.validated_data.get('muted'),
                )
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SceneNotFoundError:
            return Response(
                {'detail': 'Scene not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except SceneNotActiveError as exc:
            ack = BackgroundMusicCommandAckSerializer(
                {
                    'accepted': False,
                    'state': service.get_runtime_state(session_id),
                    'rejection_reason': 'scene_not_active',
                }
            ).data
            return Response(ack, status=status.HTTP_200_OK)
        except IngestManagerNotRunningError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        payload = BackgroundMusicCommandAckSerializer(
            {
                'accepted': ack.accepted,
                'state': ack.state,
                'rejection_reason': ack.rejection_reason,
            }
        ).data
        return Response(payload)
