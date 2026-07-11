import uuid

from django.db import models
from django.utils import timezone


class RecordingStatus(models.TextChoices):
    RECORDING = 'RECORDING', 'Recording'
    STOPPED = 'STOPPED', 'Stopped'
    FAILED = 'FAILED', 'Failed'


class SessionRecording(models.Model):
    """A composited MP4 recording for a studio session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        'studio_sessions.StudioSession',
        on_delete=models.CASCADE,
        related_name='recordings',
    )
    status = models.CharField(
        max_length=16,
        choices=RecordingStatus.choices,
        default=RecordingStatus.RECORDING,
    )
    file_path = models.CharField(max_length=512)
    started_at = models.DateTimeField(default=timezone.now)
    stopped_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'session_recordings'
        ordering = ['-started_at']

    def mark_stopped(self) -> None:
        self.status = RecordingStatus.STOPPED
        self.stopped_at = timezone.now()

    def mark_failed(self) -> None:
        self.status = RecordingStatus.FAILED
        self.stopped_at = timezone.now()
