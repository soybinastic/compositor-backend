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


class SceneItemSerializer(serializers.Serializer):
    """Scene attachment for a global Source (layout owns geometry)."""

    id = serializers.CharField(max_length=64)
    sceneId = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    sourceId = serializers.CharField(max_length=64)
    visible = serializers.BooleanField(default=True)
    zIndex = serializers.IntegerField(default=0)


class SourcesConfigSerializer(serializers.Serializer):
    version = serializers.IntegerField(required=False, min_value=1)
    items = SceneItemSerializer(many=True, required=False)
    sources = serializers.ListField(required=False)
    assignments = AssignmentsField(required=False)
