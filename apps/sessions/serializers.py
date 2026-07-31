from rest_framework import serializers

from apps.sessions.models import LayoutType, StudioSession


class CreateSessionSerializer(serializers.Serializer):
    host_display_name = serializers.CharField(max_length=120, trim_whitespace=True)
    layout = serializers.ChoiceField(
        choices=LayoutType.choices,
        default=LayoutType.CONTAIN,
        required=False,
    )

    def validate_host_display_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError('host_display_name cannot be empty')
        return value.strip()


class UpdateLayoutSerializer(serializers.Serializer):
    layout = serializers.ChoiceField(choices=LayoutType.choices)


class UpdateSessionTileConfigSerializer(serializers.Serializer):
    host_peer_id = serializers.CharField(
        max_length=64,
        required=False,
        allow_null=True,
        trim_whitespace=True,
    )
    tile_order_config = serializers.JSONField(required=False)
    hidden_source_ids = serializers.ListField(
        child=serializers.CharField(max_length=128, trim_whitespace=True),
        required=False,
    )

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError('At least one field is required.')
        return attrs

    def validate_host_peer_id(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            raise serializers.ValidationError('host_peer_id cannot be empty.')
        return value

    def validate_tile_order_config(self, value: dict) -> dict:
        if not isinstance(value, dict):
            raise serializers.ValidationError('tile_order_config must be an object.')
        if 'assignments' in value and not isinstance(value['assignments'], dict):
            raise serializers.ValidationError('assignments must be an object.')
        return value


class ValidateInviteSerializer(serializers.Serializer):
    invite_token = serializers.CharField(max_length=64)


class SessionSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source='id', read_only=True)
    room_id = serializers.CharField(read_only=True)
    active_scene_id = serializers.UUIDField(read_only=True, allow_null=True)
    countdown_state = serializers.JSONField(read_only=True, allow_null=True)

    class Meta:
        model = StudioSession
        fields = [
            'session_id',
            'room_id',
            'host_display_name',
            'host_peer_id',
            'tile_order_config',
            'hidden_source_ids',
            'layout',
            'status',
            'active_scene_id',
            'countdown_state',
            'created_at',
            'ended_at',
        ]
        read_only_fields = fields


class SessionCreateResponseSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    room_id = serializers.CharField()
    status = serializers.CharField()
    layout = serializers.CharField()
    host_display_name = serializers.CharField()
    invite_url = serializers.URLField()
    mediasoup_ws_url = serializers.CharField()
    created_at = serializers.DateTimeField()


class InviteValidationResponseSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    session_id = serializers.CharField()
    room_id = serializers.CharField()
    mediasoup_ws_url = serializers.CharField()
    layout = serializers.CharField()
    host_display_name = serializers.CharField()


class ParticipantIngestStatusSerializer(serializers.Serializer):
    participant_peer_id = serializers.CharField()
    audio_producer_id = serializers.CharField()
    video_producer_id = serializers.CharField()
    audio_port = serializers.IntegerField()
    video_port = serializers.IntegerField()
    audio_buffers = serializers.IntegerField()
    video_buffers = serializers.IntegerField()
    rtp_audio_packets = serializers.IntegerField()
    rtp_video_packets = serializers.IntegerField()
    rtcp_audio_packets = serializers.IntegerField()
    rtcp_video_packets = serializers.IntegerField()


class RtmpSourceIngestStatusSerializer(serializers.Serializer):
    source_id = serializers.CharField()
    url = serializers.CharField()
    display_name = serializers.CharField()
    video_buffers = serializers.IntegerField()
    audio_buffers = serializers.IntegerField()


class SessionIngestStatusSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    room_id = serializers.CharField()
    compositor_peer_id = serializers.CharField()
    layout = serializers.CharField()
    joined = serializers.BooleanField()
    composited_frames = serializers.IntegerField()
    canvas_width = serializers.IntegerField()
    canvas_height = serializers.IntegerField()
    host_peer_id = serializers.CharField(allow_null=True)
    recording_active = serializers.BooleanField()
    recording_file_path = serializers.CharField(allow_null=True)
    streaming_active = serializers.BooleanField()
    streaming_destination_type = serializers.CharField(allow_null=True)
    streaming_destination_url = serializers.CharField(allow_null=True)
    streaming_destination_urls = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    video_backend = serializers.CharField(allow_null=True)
    requested_video_backend = serializers.CharField()
    participants = ParticipantIngestStatusSerializer(many=True)
    rtmp_sources = RtmpSourceIngestStatusSerializer(many=True)
