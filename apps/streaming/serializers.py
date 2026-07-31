from rest_framework import serializers

from apps.streaming.models import DestinationType


class StreamDestinationInputSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=512)
    label = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')


class StartStreamSerializer(serializers.Serializer):
    destination_type = serializers.ChoiceField(choices=DestinationType.choices)
    destination_url = serializers.CharField(max_length=512, required=False, allow_blank=True)
    destination_urls = serializers.ListField(
        child=serializers.CharField(max_length=512),
        required=False,
        allow_empty=False,
    )
    destinations = StreamDestinationInputSerializer(many=True, required=False, allow_empty=False)

    def validate(self, attrs):
        destination_type = attrs['destination_type']
        if destination_type != DestinationType.RTMP:
            return attrs

        entries = self._normalize_rtmp_entries(attrs)
        if not entries:
            return attrs

        seen: set[str] = set()
        for url, _label in entries:
            lowered = url.lower()
            if not (lowered.startswith('rtmp://') or lowered.startswith('rtmps://')):
                raise serializers.ValidationError(
                    {'destination_urls': 'Each RTMP URL must start with rtmp:// or rtmps://'}
                )
            if url in seen:
                raise serializers.ValidationError(
                    {'destination_urls': 'Duplicate RTMP URLs are not allowed'}
                )
            seen.add(url)

        attrs['_rtmp_entries'] = entries
        return attrs

    @staticmethod
    def _normalize_rtmp_entries(attrs: dict) -> list[tuple[str, str]]:
        if attrs.get('destinations'):
            return [
                (item['url'].strip(), (item.get('label') or '').strip())
                for item in attrs['destinations']
                if item['url'].strip()
            ]

        if attrs.get('destination_urls'):
            return [(url.strip(), '') for url in attrs['destination_urls'] if url.strip()]

        single = (attrs.get('destination_url') or '').strip()
        if single:
            return [(single, '')]

        return []


class StreamDestinationSerializer(serializers.Serializer):
    destination_id = serializers.UUIDField()
    url = serializers.CharField()
    label = serializers.CharField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField()
    stopped_at = serializers.DateTimeField(allow_null=True)


class StreamSerializer(serializers.Serializer):
    stream_id = serializers.UUIDField()
    session_id = serializers.UUIDField()
    destination_type = serializers.CharField()
    destination_url = serializers.CharField()
    destination_urls = serializers.ListField(child=serializers.CharField())
    destinations = StreamDestinationSerializer(many=True)
    output_path = serializers.CharField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField()
    stopped_at = serializers.DateTimeField(allow_null=True)
