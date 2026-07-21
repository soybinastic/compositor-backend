from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.graphics.serializers import (
    BackgroundSerializer,
    BannerSerializer,
    BannerTickerSerializer,
    BulkGraphicsSerializer,
    ChatSerializer,
    GraphicsStateSerializer,
    LogoSerializer,
    OverlaySerializer,
    QrSerializer,
    TickerSerializer,
)
from apps.graphics.service import GraphicsService
from apps.sessions.exceptions import SessionEndedError, SessionNotFoundError


def _graphics_service() -> GraphicsService:
    return GraphicsService()


def _handle_session_errors(exc):
    if isinstance(exc, SessionNotFoundError):
        return Response({'detail': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, SessionEndedError):
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
    raise exc


class SessionGraphicsView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            state = _graphics_service().get_graphics(session_id)
        except SessionNotFoundError as exc:
            return _handle_session_errors(exc)
        return Response(GraphicsStateSerializer(state).data)


class SessionGraphicsBulkView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        serializer = BulkGraphicsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        partial = {
            key: value
            for key, value in serializer.validated_data.items()
            if key in serializer.validated_data
        }
        # Include explicit nulls from raw body for clear-layer semantics.
        for key in BulkGraphicsSerializer().fields:
            if key in request.data and request.data[key] is None:
                partial[key] = None
        try:
            state = _graphics_service().update_bulk(session_id, partial)
        except (SessionNotFoundError, SessionEndedError) as exc:
            return _handle_session_errors(exc)
        return Response(GraphicsStateSerializer(state).data)


class _LayerUpdateView(APIView):
    authentication_classes = []
    permission_classes = []
    layer: str = ''
    serializer_class = None

    def post(self, request, session_id):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            state = _graphics_service().update_layer(
                session_id,
                self.layer,
                dict(serializer.validated_data),
            )
        except (SessionNotFoundError, SessionEndedError) as exc:
            return _handle_session_errors(exc)
        return Response(GraphicsStateSerializer(state).data)


class SessionGraphicsBackgroundView(_LayerUpdateView):
    layer = 'background'
    serializer_class = BackgroundSerializer


class SessionGraphicsOverlayView(_LayerUpdateView):
    layer = 'overlay'
    serializer_class = OverlaySerializer


class SessionGraphicsLogoView(_LayerUpdateView):
    layer = 'logo'
    serializer_class = LogoSerializer


class SessionGraphicsQrView(_LayerUpdateView):
    layer = 'qr'
    serializer_class = QrSerializer


class SessionGraphicsBannerView(_LayerUpdateView):
    layer = 'banner'
    serializer_class = BannerSerializer


class SessionGraphicsTickerView(_LayerUpdateView):
    layer = 'ticker'
    serializer_class = TickerSerializer


class SessionGraphicsBannerTickerView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        serializer = BannerTickerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            state = _graphics_service().update_banner_ticker(
                session_id,
                banner=data.get('banner'),
                ticker=data.get('ticker'),
            )
        except (SessionNotFoundError, SessionEndedError) as exc:
            return _handle_session_errors(exc)
        return Response(GraphicsStateSerializer(state).data)


class SessionGraphicsChatView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        serializer = ChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            state = _graphics_service().update_chat(
                session_id,
                dict(serializer.validated_data),
            )
        except (SessionNotFoundError, SessionEndedError) as exc:
            return _handle_session_errors(exc)
        return Response(GraphicsStateSerializer(state).data)
