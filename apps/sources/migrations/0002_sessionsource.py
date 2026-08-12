"""Migrations for SessionRtmpSource and SessionSource."""

import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('sources', '0001_initial'),
        ('studio_sessions', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SessionSource',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_id', models.CharField(max_length=64)),
                (
                    'type',
                    models.CharField(
                        choices=[
                            ('camera', 'Camera'),
                            ('screen', 'Screen Share'),
                            ('prerecorded', 'Pre-recorded Video'),
                            ('image', 'Image'),
                            ('rtmp', 'RTMP'),
                            ('audio', 'Audio'),
                            ('pdf', 'PDF'),
                        ],
                        max_length=32,
                    ),
                ),
                ('name', models.CharField(max_length=120)),
                (
                    'state',
                    models.CharField(
                        choices=[
                            ('LOADING', 'Loading'),
                            ('ACTIVE', 'Active'),
                            ('PAUSED', 'Paused'),
                            ('STOPPED', 'Stopped'),
                        ],
                        default='STOPPED',
                        max_length=16,
                    ),
                ),
                ('volume', models.FloatField(default=1.0)),
                ('muted', models.BooleanField(default=False)),
                ('settings', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('stopped_at', models.DateTimeField(blank=True, null=True)),
                (
                    'session',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='studio_sources',
                        to='studio_sessions.studiosession',
                    ),
                ),
            ],
            options={
                'db_table': 'session_studio_sources',
                'ordering': ['created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='sessionsource',
            constraint=models.UniqueConstraint(
                fields=('session', 'source_id'),
                name='unique_session_studio_source_id',
            ),
        ),
    ]
