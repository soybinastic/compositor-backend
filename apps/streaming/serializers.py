from rest_framework import serializers

from apps.streaming.models import DestinationType


class StartStreamSerializer(serializers.Serializer):
    destination_type = serializers.ChoiceField(choices=DestinationType.choices)
    destination_url = serializers.CharField(max_length=512, required=False, allow_blank=True)


class StreamSerializer(serializers.Serializer):
    stream_id = serializers.UUIDField()
    session_id = serializers.UUIDField()
    destination_type = serializers.CharField()
    destination_url = serializers.CharField()
    output_path = serializers.CharField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField()
    stopped_at = serializers.DateTimeField(allow_null=True)
