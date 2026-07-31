import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('streaming', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='StreamDestination',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('url', models.CharField(max_length=512)),
                ('label', models.CharField(blank=True, max_length=64)),
                ('status', models.CharField(choices=[('LIVE', 'Live'), ('STOPPED', 'Stopped'), ('FAILED', 'Failed')], default='LIVE', max_length=16)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('stopped_at', models.DateTimeField(blank=True, null=True)),
                ('stream', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='destinations', to='streaming.sessionstream')),
            ],
            options={
                'db_table': 'stream_destinations',
                'ordering': ['started_at'],
            },
        ),
    ]
