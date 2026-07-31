import uuid

from django.db import models

from apps.scenes.constants import (
    DEFAULT_BACKGROUND_MUSIC_CONFIG,
    DEFAULT_DEVICES_CONFIG,
    DEFAULT_SOURCES_CONFIG,
)
from apps.sessions.models import LayoutType


class SceneType(models.TextChoices):
    CAMERA = 'CAMERA', 'Camera'
    COUNTDOWN = 'COUNTDOWN', 'Countdown'


class StudioScene(models.Model):
    """A saved snapshot of studio look and behavior within a session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        'studio_sessions.StudioSession',
        on_delete=models.CASCADE,
        related_name='scenes',
    )
    name = models.CharField(max_length=120)
    scene_type = models.CharField(max_length=16, choices=SceneType.choices)
    sort_order = models.IntegerField(default=0)

    layout = models.CharField(
        max_length=32,
        choices=LayoutType.choices,
        blank=True,
        default='',
    )
    graphics_config = models.JSONField(default=dict, blank=True)
    devices_config = models.JSONField(default=dict, blank=True)
    sources_config = models.JSONField(default=dict, blank=True)
    background_music_config = models.JSONField(default=dict, blank=True)

    countdown_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    countdown_target_scene = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='countdown_references',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'studio_scenes'
        ordering = ['sort_order', 'created_at']

    def save(self, *args, **kwargs):
        if not self.devices_config:
            self.devices_config = dict(DEFAULT_DEVICES_CONFIG)
        if not self.sources_config:
            self.sources_config = dict(DEFAULT_SOURCES_CONFIG)
        if not self.background_music_config:
            self.background_music_config = dict(DEFAULT_BACKGROUND_MUSIC_CONFIG)
        super().save(*args, **kwargs)
