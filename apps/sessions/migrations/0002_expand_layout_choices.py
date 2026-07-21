# Generated manually for expanded layout choices.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('studio_sessions', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='studiosession',
            name='layout',
            field=models.CharField(
                choices=[
                    ('CONTAIN', 'Contain'),
                    ('COVER', 'Cover'),
                    ('THUMBNAIL', 'Thumbnail'),
                    ('GRID', 'Grid'),
                    ('SIDE_BY_SIDE', 'Side by side'),
                    ('HALFSCREEN', 'Half screen'),
                    ('SPOTLIGHT', 'Spotlight'),
                    ('CINEMA', 'Cinema'),
                    ('PICTURE_IN_PICTURE', 'Picture in picture'),
                    ('OVERLAY', 'Overlay'),
                    ('FULLSCREEN', 'Fullscreen'),
                ],
                default='CONTAIN',
                max_length=32,
            ),
        ),
    ]
