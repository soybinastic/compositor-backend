from rest_framework import serializers

from apps.sources.models import SourceType


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


class CreateSourceSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=SourceType.choices)
    name = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
    settings = serializers.DictField(required=False, default=dict)
    volume = serializers.FloatField(required=False, min_value=0.0, max_value=1.0, default=1.0)
    muted = serializers.BooleanField(required=False, default=False)
    start = serializers.BooleanField(required=False, default=True)


class UpdateSourceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    settings = serializers.DictField(required=False)
    volume = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)
    muted = serializers.BooleanField(required=False)


class SourceSerializer(serializers.Serializer):
    source_id = serializers.CharField()
    session_id = serializers.UUIDField()
    type = serializers.CharField()
    name = serializers.CharField()
    state = serializers.CharField()
    volume = serializers.FloatField()
    muted = serializers.BooleanField()
    settings = serializers.DictField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class AttachSourceSerializer(serializers.Serializer):
    source_id = serializers.CharField(max_length=64)
    visible = serializers.BooleanField(required=False, default=True)


class ReorderSceneItemsSerializer(serializers.Serializer):
    source_ids = serializers.ListField(
        child=serializers.CharField(max_length=64),
        allow_empty=True,
    )


class SeekSourceSerializer(serializers.Serializer):
    position_ms = serializers.FloatField(min_value=0.0)


class SetVisibilitySerializer(serializers.Serializer):
    visible = serializers.BooleanField()
