from rest_framework import serializers


class RecordingSerializer(serializers.Serializer):
    recording_id = serializers.UUIDField()
    session_id = serializers.UUIDField()
    status = serializers.CharField()
    file_path = serializers.CharField()
    started_at = serializers.DateTimeField()
    stopped_at = serializers.DateTimeField(allow_null=True)
