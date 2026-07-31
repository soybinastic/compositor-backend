from rest_framework import serializers

from apps.scenes.constants import (
    DEFAULT_BACKGROUND_MUSIC_CONFIG,
    DEFAULT_DEVICES_CONFIG,
    DEFAULT_SOURCES_CONFIG,
)
from apps.scenes.models import SceneType, StudioScene
from apps.sessions.models import LayoutType


class DevicesConfigSerializer(serializers.Serializer):
    cameraId = serializers.CharField(required=False, allow_null=True, default=None)
    microphoneId = serializers.CharField(required=False, allow_null=True, default=None)
    speakerId = serializers.CharField(required=False, allow_null=True, default=None)


class CreateCameraSceneSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[SceneType.CAMERA])
    devices = DevicesConfigSerializer(required=False)
    layout = serializers.ChoiceField(choices=LayoutType.choices, required=False)
    graphics_config = serializers.JSONField(required=False)


class CreateCountdownSceneSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[SceneType.COUNTDOWN])
    duration_seconds = serializers.IntegerField(min_value=1, max_value=3600)
    target_scene_id = serializers.UUIDField()


class CreateSceneSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=SceneType.choices)
    devices = DevicesConfigSerializer(required=False)
    layout = serializers.ChoiceField(choices=LayoutType.choices, required=False)
    graphics_config = serializers.JSONField(required=False)
    duration_seconds = serializers.IntegerField(min_value=1, max_value=3600, required=False)
    target_scene_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        scene_type = attrs['type']
        if scene_type == SceneType.CAMERA:
            return attrs
        if scene_type == SceneType.COUNTDOWN:
            if not attrs.get('duration_seconds'):
                raise serializers.ValidationError(
                    {'duration_seconds': 'Required for countdown scenes.'}
                )
            if not attrs.get('target_scene_id'):
                raise serializers.ValidationError(
                    {'target_scene_id': 'Required for countdown scenes.'}
                )
        return attrs


class UpdateSceneSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, trim_whitespace=True, required=False)
    layout = serializers.ChoiceField(choices=LayoutType.choices, required=False)
    graphics_config = serializers.JSONField(required=False)
    devices = DevicesConfigSerializer(required=False)
    sources = serializers.JSONField(required=False)
    background_music = serializers.JSONField(required=False)


class SceneSerializer(serializers.ModelSerializer):
    scene_id = serializers.UUIDField(source='id', read_only=True)
    session_id = serializers.UUIDField(read_only=True)
    type = serializers.CharField(source='scene_type', read_only=True)
    devices = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    background_music = serializers.SerializerMethodField()
    countdown = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = StudioScene
        fields = [
            'scene_id',
            'session_id',
            'name',
            'type',
            'sort_order',
            'layout',
            'graphics_config',
            'devices',
            'sources',
            'background_music',
            'countdown',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_devices(self, obj: StudioScene) -> dict:
        return obj.devices_config or dict(DEFAULT_DEVICES_CONFIG)

    def get_sources(self, obj: StudioScene) -> dict:
        return obj.sources_config or dict(DEFAULT_SOURCES_CONFIG)

    def get_background_music(self, obj: StudioScene) -> dict:
        return obj.background_music_config or dict(DEFAULT_BACKGROUND_MUSIC_CONFIG)

    def get_countdown(self, obj: StudioScene) -> dict | None:
        if obj.scene_type != SceneType.COUNTDOWN:
            return None
        return {
            'duration_seconds': obj.countdown_duration_seconds,
            'target_scene_id': (
                str(obj.countdown_target_scene_id)
                if obj.countdown_target_scene_id
                else None
            ),
        }

    def get_is_active(self, obj: StudioScene) -> bool:
        active_scene_id = self.context.get('active_scene_id')
        if active_scene_id is None:
            return False
        return str(obj.id) == str(active_scene_id)


class SceneActivateResponseSerializer(serializers.Serializer):
    """Documentation-only schema for activate response."""

    scene = serializers.JSONField()
    layout = serializers.CharField()
    graphics_config = serializers.JSONField()
    devices = serializers.JSONField()
