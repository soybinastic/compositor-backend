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


class ValidateInviteSerializer(serializers.Serializer):
    invite_token = serializers.CharField(max_length=64)


class SessionSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source='id', read_only=True)
    room_id = serializers.CharField(read_only=True)

    class Meta:
        model = StudioSession
        fields = [
            'session_id',
            'room_id',
            'host_display_name',
            'layout',
            'status',
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
    participants = ParticipantIngestStatusSerializer(many=True)
