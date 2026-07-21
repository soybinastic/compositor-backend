from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.compositor.gstreamer import check_gstreamer
from apps.compositor.health import check_mediasoup
from apps.compositor.metrics import collect_metrics
from apps.compositor.registry import get as get_ingest_manager
from apps.sessions.exceptions import (
    InvalidInviteTokenError,
    SessionEndedError,
    SessionNotFoundError,
)
from apps.sessions.serializers import (
    CreateSessionSerializer,
    InviteValidationResponseSerializer,
    SessionCreateResponseSerializer,
    SessionIngestStatusSerializer,
    SessionSerializer,
    UpdateLayoutSerializer,
    ValidateInviteSerializer,
)
from apps.sessions.services.session_service import SessionService


def _session_service() -> SessionService:
    return SessionService()


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        gstreamer = check_gstreamer()
        mediasoup = check_mediasoup()
        metrics = collect_metrics()

        healthy = gstreamer['available'] and mediasoup['available']
        payload = {
            'status': 'ok' if healthy else 'degraded',
            'service': 'compositor-backend',
            'gstreamer': gstreamer,
            'mediasoup': mediasoup,
            'metrics': metrics,
        }
        http_status = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=http_status)


class MetricsView(APIView):
    """Runtime metrics for monitoring."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(collect_metrics())


class SessionListCreateView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = CreateSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = _session_service().create_session(
            host_display_name=serializer.validated_data['host_display_name'],
            layout=serializer.validated_data.get('layout'),
        )
        session = result.session

        response_data = {
            'session_id': session.id,
            'room_id': session.room_id,
            'status': session.status,
            'layout': session.layout,
            'host_display_name': session.host_display_name,
            'invite_url': result.invite_url,
            'mediasoup_ws_url': result.mediasoup_ws_url,
            'created_at': session.created_at,
        }
        return Response(
            SessionCreateResponseSerializer(response_data).data,
            status=status.HTTP_201_CREATED,
        )


class SessionDetailView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            session = _session_service().get_session(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(SessionSerializer(session).data)

    def delete(self, request, session_id):
        try:
            session = _session_service().end_session(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(SessionSerializer(session).data)


class SessionLayoutView(APIView):
    authentication_classes = []
    permission_classes = []

    def patch(self, request, session_id):
        serializer = UpdateLayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            session = _session_service().update_layout(
                session_id,
                serializer.validated_data['layout'],
            )
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(SessionSerializer(session).data)


class ValidateInviteView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        serializer = ValidateInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = _session_service().validate_invite(
                session_id,
                serializer.validated_data['invite_token'],
            )
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SessionEndedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except InvalidInviteTokenError:
            return Response(
                {'detail': 'Invalid invite token'},
                status=status.HTTP_403_FORBIDDEN,
            )

        response_data = {
            'valid': True,
            'session_id': result.session_id,
            'room_id': result.room_id,
            'mediasoup_ws_url': result.mediasoup_ws_url,
            'layout': result.layout,
            'host_display_name': result.host_display_name,
        }
        return Response(InviteValidationResponseSerializer(response_data).data)


class SessionIngestView(APIView):
    """Debug endpoint: RTP ingest status for a session."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, session_id):
        try:
            _session_service().get_session(session_id)
        except SessionNotFoundError:
            return Response(
                {'detail': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        manager = get_ingest_manager(str(session_id))
        if manager is None:
            return Response(
                {'detail': 'Ingest manager not running for this session'},
                status=status.HTTP_404_NOT_FOUND,
            )

        ingest_status = manager.get_status()
        return Response(
            SessionIngestStatusSerializer(
                {
                    'session_id': ingest_status.session_id,
                    'room_id': ingest_status.room_id,
                    'compositor_peer_id': ingest_status.compositor_peer_id,
                    'layout': ingest_status.layout,
                    'joined': ingest_status.joined,
                    'composited_frames': ingest_status.composited_frames,
                    'canvas_width': ingest_status.canvas_width,
                    'canvas_height': ingest_status.canvas_height,
                    'host_peer_id': ingest_status.host_peer_id,
                    'recording_active': ingest_status.recording_active,
                    'recording_file_path': ingest_status.recording_file_path,
                    'streaming_active': ingest_status.streaming_active,
                    'streaming_destination_type': ingest_status.streaming_destination_type,
                    'streaming_destination_url': ingest_status.streaming_destination_url,
                    'participants': [
                        {
                            'participant_peer_id': p.participant_peer_id,
                            'audio_producer_id': p.audio_producer_id,
                            'video_producer_id': p.video_producer_id,
                            'audio_port': p.audio_port,
                            'video_port': p.video_port,
                            'audio_buffers': p.audio_buffers,
                            'video_buffers': p.video_buffers,
                            'rtp_audio_packets': p.rtp_audio_packets,
                            'rtp_video_packets': p.rtp_video_packets,
                            'rtcp_audio_packets': p.rtcp_audio_packets,
                            'rtcp_video_packets': p.rtcp_video_packets,
                        }
                        for p in ingest_status.participants
                    ],
                    'rtmp_sources': [
                        {
                            'source_id': source.source_id,
                            'url': source.url,
                            'display_name': source.display_name,
                            'video_buffers': source.video_buffers,
                            'audio_buffers': source.audio_buffers,
                        }
                        for source in ingest_status.rtmp_sources
                    ],
                }
            ).data
        )
