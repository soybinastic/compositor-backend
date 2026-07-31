from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('studio_sessions', '0005_studiosession_countdown_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='studiosession',
            name='host_peer_id',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='studiosession',
            name='tile_order_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='studiosession',
            name='hidden_source_ids',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
