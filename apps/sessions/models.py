import uuid

from django.db import models
from django.utils import timezone


class SessionStatus(models.TextChoices):
    CREATED = 'CREATED', 'Created'
    ACTIVE = 'ACTIVE', 'Active'
    ENDED = 'ENDED', 'Ended'


class LayoutType(models.TextChoices):
    CONTAIN = 'CONTAIN', 'Contain'
    THUMBNAIL = 'THUMBNAIL', 'Thumbnail'


class StudioSession(models.Model):
    """
    A studio session. The session UUID doubles as the mediasoup roomId.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    host_display_name = models.CharField(max_length=120)
    invite_token = models.CharField(max_length=64, unique=True)
    layout = models.CharField(
        max_length=16,
        choices=LayoutType.choices,
        default=LayoutType.CONTAIN,
    )
    status = models.CharField(
        max_length=16,
        choices=SessionStatus.choices,
        default=SessionStatus.CREATED,
    )
    mediasoup_compositor_peer_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'studio_sessions'
        ordering = ['-created_at']

    @property
    def room_id(self) -> str:
        return str(self.id)

    def end(self) -> None:
        self.status = SessionStatus.ENDED
        self.ended_at = timezone.now()
