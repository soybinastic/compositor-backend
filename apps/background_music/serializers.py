from rest_framework import serializers


class BackgroundMusicErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class BackgroundMusicRuntimeStateSerializer(serializers.Serializer):
    scene_id = serializers.CharField(allow_null=True)
    playback_state = serializers.CharField()
    position_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    error = BackgroundMusicErrorSerializer(allow_null=True)
    updated_at = serializers.CharField()


class BackgroundMusicCommandAckSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    state = BackgroundMusicRuntimeStateSerializer()
    rejection_reason = serializers.CharField(required=False, allow_null=True)


class BackgroundMusicVolumeSerializer(serializers.Serializer):
    volume = serializers.FloatField(min_value=0.0, max_value=1.0)
    muted = serializers.BooleanField(required=False)
