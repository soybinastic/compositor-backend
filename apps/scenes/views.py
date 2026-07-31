from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.scenes.exceptions import (
    ActiveSceneDeleteError,
    CountdownSceneNotActivatableError,
    InvalidCountdownTargetError,
    SceneNotFoundError,
)
from apps.scenes.serializers import (
    CreateSceneSerializer,
    SceneSerializer,
    UpdateSceneSerializer,
)
from apps.scenes.service import SceneService
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError


def _scene_service() -> SceneService:
    return SceneService()


def _serialize_scene(scene, session) -> dict:
    return SceneSerializer(
        scene,
        context={'active_scene_id': session.active_scene_id},
    ).data


class SessionSceneListCreateView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            session_service_scenes = _scene_service()
            from apps.sessions.services.session_service import SessionService

            session = SessionService().get_session(session_id)
            scenes = session_service_scenes.list_scenes(session_id)
            session.refresh_from_db()
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            SceneSerializer(
                scenes,
                many=True,
                context={'active_scene_id': session.active_scene_id},
            ).data
        )

    def post(self, request, session_id):
        serializer = CreateSceneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            from apps.sessions.services.session_service import SessionService

            session = SessionService().get_session(session_id)
            scene = _scene_service().create_scene(
                session_id,
                scene_type=data['type'],
                devices=data.get('devices'),
                layout=data.get('layout'),
                graphics_config=data.get('graphics_config'),
                duration_seconds=data.get('duration_seconds'),
                target_scene_id=data.get('target_scene_id'),
            )
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except InvalidCountdownTargetError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            _serialize_scene(scene, session),
            status=status.HTTP_201_CREATED,
        )


class SessionSceneDetailView(APIView):
    authentication_classes = []
    permission_classes = []

    def patch(self, request, session_id, scene_id):
        serializer = UpdateSceneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            from apps.sessions.services.session_service import SessionService

            session = SessionService().get_session(session_id)
            scene = _scene_service().update_scene(
                session_id,
                scene_id,
                name=data.get('name'),
                layout=data.get('layout'),
                graphics_config=data.get('graphics_config'),
                devices=data.get('devices'),
                sources_config=data.get('sources'),
                background_music_config=data.get('background_music'),
            )
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SceneNotFoundError:
            return Response({'detail': 'Scene not found'}, status=status.HTTP_404_NOT_FOUND)
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialize_scene(scene, session))

    def delete(self, request, session_id, scene_id):
        try:
            _scene_service().delete_scene(session_id, scene_id)
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SceneNotFoundError:
            return Response({'detail': 'Scene not found'}, status=status.HTTP_404_NOT_FOUND)
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except ActiveSceneDeleteError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionSceneActivateView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, scene_id):
        try:
            from apps.sessions.services.session_service import SessionService

            session = SessionService().get_session(session_id)
            scene = _scene_service().activate_scene(session_id, scene_id)
            session.refresh_from_db()
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SceneNotFoundError:
            return Response({'detail': 'Scene not found'}, status=status.HTTP_404_NOT_FOUND)
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except CountdownSceneNotActivatableError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            'scene': _serialize_scene(scene, session),
            'layout': session.layout,
            'graphics_config': scene.graphics_config,
            'devices': scene.devices_config or {},
        }
        return Response(payload)
