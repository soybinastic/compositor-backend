import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('studio_sessions', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SessionRtmpSource',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_id', models.CharField(max_length=64)),
                ('url', models.CharField(max_length=512)),
                ('display_name', models.CharField(blank=True, max_length=120)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('STOPPED', 'Stopped'), ('FAILED', 'Failed')], default='ACTIVE', max_length=16)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('stopped_at', models.DateTimeField(blank=True, null=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rtmp_sources', to='studio_sessions.studiosession')),
            ],
            options={
                'db_table': 'session_rtmp_sources',
                'ordering': ['-started_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='sessionrtmpsource',
            constraint=models.UniqueConstraint(fields=('session', 'source_id'), name='unique_session_rtmp_source_id'),
        ),
    ]
