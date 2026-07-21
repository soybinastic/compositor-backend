# Generated manually for graphics_config JSONField.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('studio_sessions', '0002_expand_layout_choices'),
    ]

    operations = [
        migrations.AddField(
            model_name='studiosession',
            name='graphics_config',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
