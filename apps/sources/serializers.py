from rest_framework import serializers


class AddRtmpSourceSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=512, trim_whitespace=True)
    display_name = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
        default='',
    )

    def validate_url(self, value: str) -> str:
        lowered = value.lower()
        if not (lowered.startswith('rtmp://') or lowered.startswith('rtmps://')):
            raise serializers.ValidationError(
                'url must start with rtmp:// or rtmps://'
            )
        return value


class RtmpSourceSerializer(serializers.Serializer):
    source_id = serializers.CharField()
    session_id = serializers.UUIDField()
    url = serializers.CharField()
    display_name = serializers.CharField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField()
    stopped_at = serializers.DateTimeField(allow_null=True)
    video_buffers = serializers.IntegerField(required=False, default=0)
    audio_buffers = serializers.IntegerField(required=False, default=0)
