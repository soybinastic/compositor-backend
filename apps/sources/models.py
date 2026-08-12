import uuid

from django.db import models
from django.utils import timezone


class RtmpSourceStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    STOPPED = 'STOPPED', 'Stopped'
    FAILED = 'FAILED', 'Failed'


class SourceType(models.TextChoices):
    CAMERA = 'camera', 'Camera'
    SCREEN = 'screen', 'Screen Share'
    PRERECORDED = 'prerecorded', 'Pre-recorded Video'
    IMAGE = 'image', 'Image'
    RTMP = 'rtmp', 'RTMP'
    AUDIO = 'audio', 'Audio'
    PDF = 'pdf', 'PDF'


class SourceState(models.TextChoices):
    LOADING = 'LOADING', 'Loading'
    ACTIVE = 'ACTIVE', 'Active'
    PAUSED = 'PAUSED', 'Paused'
    STOPPED = 'STOPPED', 'Stopped'


class SessionRtmpSource(models.Model):
    """An external RTMP pull source ingested into the compositor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        'studio_sessions.StudioSession',
        on_delete=models.CASCADE,
        related_name='rtmp_sources',
    )
    source_id = models.CharField(max_length=64)
    url = models.CharField(max_length=512)
    display_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=16,
        choices=RtmpSourceStatus.choices,
        default=RtmpSourceStatus.ACTIVE,
    )
    started_at = models.DateTimeField(default=timezone.now)
    stopped_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'session_rtmp_sources'
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'source_id'],
                name='unique_session_rtmp_source_id',
            ),
        ]

    def mark_stopped(self) -> None:
        self.status = RtmpSourceStatus.STOPPED
        self.stopped_at = timezone.now()

    def mark_failed(self) -> None:
        self.status = RtmpSourceStatus.FAILED
        self.stopped_at = timezone.now()


class SessionSource(models.Model):
    """Session-scoped reusable Studio Source (global runtime registry)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        'studio_sessions.StudioSession',
        on_delete=models.CASCADE,
        related_name='studio_sources',
    )
    source_id = models.CharField(max_length=64)
    type = models.CharField(max_length=32, choices=SourceType.choices)
    name = models.CharField(max_length=120)
    state = models.CharField(
        max_length=16,
        choices=SourceState.choices,
        default=SourceState.STOPPED,
    )
    volume = models.FloatField(default=1.0)
    muted = models.BooleanField(default=False)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    stopped_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'session_studio_sources'
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'source_id'],
                name='unique_session_studio_source_id',
            ),
        ]

    def mark_state(self, state: str) -> None:
        self.state = state
        if state == SourceState.STOPPED:
            self.stopped_at = timezone.now()
        self.updated_at = timezone.now()
