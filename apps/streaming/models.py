import uuid

from django.db import models
from django.utils import timezone


class DestinationType(models.TextChoices):
    RTMP = 'RTMP', 'RTMP'
    HLS = 'HLS', 'HLS'


class StreamStatus(models.TextChoices):
    LIVE = 'LIVE', 'Live'
    STOPPED = 'STOPPED', 'Stopped'
    FAILED = 'FAILED', 'Failed'


class SessionStream(models.Model):
    """A live stream egress from the compositor output."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        'studio_sessions.StudioSession',
        on_delete=models.CASCADE,
        related_name='streams',
    )
    destination_type = models.CharField(
        max_length=8,
        choices=DestinationType.choices,
    )
    destination_url = models.CharField(max_length=512, blank=True)
    output_path = models.CharField(max_length=512, blank=True)
    status = models.CharField(
        max_length=16,
        choices=StreamStatus.choices,
        default=StreamStatus.LIVE,
    )
    started_at = models.DateTimeField(default=timezone.now)
    stopped_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'session_streams'
        ordering = ['-started_at']

    def mark_stopped(self) -> None:
        self.status = StreamStatus.STOPPED
        self.stopped_at = timezone.now()

    def mark_failed(self) -> None:
        self.status = StreamStatus.FAILED
        self.stopped_at = timezone.now()
