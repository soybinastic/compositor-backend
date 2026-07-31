from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('studio_sessions', '0004_studiosession_active_scene'),
    ]

    operations = [
        migrations.AddField(
            model_name='studiosession',
            name='countdown_state',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
