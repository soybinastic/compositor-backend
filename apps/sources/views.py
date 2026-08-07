from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError
from apps.sources.exceptions import (
    IngestManagerNotRunningError,
    InvalidRtmpUrlError,
    RtmpSourceNotFoundError,
    SourceAlreadyAttachedError,
    SourceNotFoundError,
    SourceTypeNotImplementedError,
    UnsupportedSourceTypeError,
)
from apps.sources.serializers import (
    AddRtmpSourceSerializer,
    AttachSourceSerializer,
    CreateSourceSerializer,
    ReorderSceneItemsSerializer,
    RtmpSourceSerializer,
    SeekSourceSerializer,
    SetVisibilitySerializer,
    SourceSerializer,
    UpdateSourceSerializer,
)
from apps.sources.service import RtmpSourceService
from apps.sources.source_service import SourceService


def _rtmp_source_service() -> RtmpSourceService:
    return RtmpSourceService()


def _source_service() -> SourceService:
    return SourceService()


def _serialize_rtmp(source) -> dict:
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


def _serialize_source(source) -> dict:
    return {
        'source_id': source.source_id,
        'session_id': source.session_id,
        'type': source.type,
        'name': source.name,
        'state': source.state,
        'volume': source.volume,
        'muted': source.muted,
        'settings': source.settings,
        'created_at': source.created_at,
        'updated_at': source.updated_at,
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
                [_serialize_rtmp(source) for source in sources],
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
            RtmpSourceSerializer(_serialize_rtmp(source)).data,
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

        return Response(RtmpSourceSerializer(_serialize_rtmp(source)).data)


class SessionSourceListCreateView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            sources = _source_service().list_sources(session_id)
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(SourceSerializer([_serialize_source(s) for s in sources], many=True).data)

    def post(self, request, session_id):
        serializer = CreateSourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            source = _source_service().create_source(
                session_id,
                source_type=data['type'],
                name=data.get('name', ''),
                settings=data.get('settings') or {},
                volume=data.get('volume', 1.0),
                muted=data.get('muted', False),
                start=data.get('start', True),
            )
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except UnsupportedSourceTypeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except SourceTypeNotImplementedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            SourceSerializer(_serialize_source(source)).data,
            status=status.HTTP_201_CREATED,
        )


class SessionSourceDetailView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id, source_id):
        try:
            source = _source_service().get_source(session_id, source_id)
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SourceNotFoundError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(SourceSerializer(_serialize_source(source)).data)

    def patch(self, request, session_id, source_id):
        serializer = UpdateSourceSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            source = _source_service().update_source(
                session_id,
                source_id,
                **serializer.validated_data,
            )
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SourceNotFoundError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(SourceSerializer(_serialize_source(source)).data)

    def delete(self, request, session_id, source_id):
        try:
            _source_service().delete_source(session_id, source_id)
        except SessionNotFoundError:
            return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        except SourceNotFoundError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionSourcePlayView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, source_id):
        try:
            source = _source_service().play(session_id, source_id)
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SourceSerializer(_serialize_source(source)).data)


class SessionSourcePauseView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, source_id):
        try:
            source = _source_service().pause(session_id, source_id)
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SourceSerializer(_serialize_source(source)).data)


class SessionSourceStopView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, source_id):
        try:
            source = _source_service().stop_playback(session_id, source_id)
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SourceSerializer(_serialize_source(source)).data)


class SessionSourceSeekView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, source_id):
        serializer = SeekSourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            source = _source_service().seek(
                session_id,
                source_id,
                position_ms=serializer.validated_data['position_ms'],
            )
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SourceSerializer(_serialize_source(source)).data)


class SceneSourceAttachView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, scene_id):
        serializer = AttachSourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            config = _source_service().attach_to_scene(
                session_id,
                scene_id,
                serializer.validated_data['source_id'],
                visible=serializer.validated_data.get('visible', True),
            )
        except SourceAlreadyAttachedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(config, status=status.HTTP_201_CREATED)


class SceneSourceDetachView(APIView):
    authentication_classes = []
    permission_classes = []

    def delete(self, request, session_id, scene_id, source_id):
        try:
            config = _source_service().detach_from_scene(session_id, scene_id, source_id)
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(config)


class SceneSourceVisibilityView(APIView):
    authentication_classes = []
    permission_classes = []

    def patch(self, request, session_id, scene_id, source_id):
        serializer = SetVisibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            config = _source_service().set_item_visibility(
                session_id,
                scene_id,
                source_id,
                visible=serializer.validated_data['visible'],
            )
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(config)


class SceneSourceReorderView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id, scene_id):
        serializer = ReorderSceneItemsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            config = _source_service().reorder_scene_items(
                session_id,
                scene_id,
                serializer.validated_data['source_ids'],
            )
        except (SessionNotFoundError, SourceNotFoundError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(config)
