import uuid

from django.db import models
from django.utils import timezone


class RtmpSourceStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    STOPPED = 'STOPPED', 'Stopped'
    FAILED = 'FAILED', 'Failed'


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
