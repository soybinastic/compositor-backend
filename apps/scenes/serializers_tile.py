"""Serializers for scene tile / sources config."""

from rest_framework import serializers

from apps.compositor.tile_order import sanitize_assignments_for_storage


class AssignmentsField(serializers.Field):
    """Slot index → source id map (JSON keys may be strings)."""

    def to_representation(self, value):
        if not value:
            return {}
        return dict(value)

    def to_internal_value(self, data):
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise serializers.ValidationError('Expected an object.')
        return sanitize_assignments_for_storage(data)


class SourcesConfigSerializer(serializers.Serializer):
    version = serializers.IntegerField(required=False, min_value=1)
    sources = serializers.ListField(required=False)
    assignments = AssignmentsField(required=False)
