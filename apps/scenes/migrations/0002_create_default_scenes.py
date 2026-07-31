"""Create default Scene 1 for existing sessions."""

from django.db import migrations


def create_default_scenes(apps, schema_editor):
    StudioSession = apps.get_model('studio_sessions', 'StudioSession')
    StudioScene = apps.get_model('scenes', 'StudioScene')

    empty_graphics = {
        'background': None,
        'overlay': None,
        'logo': None,
        'qr': None,
        'banner': None,
        'ticker': None,
        'chat': None,
    }
    default_devices = {
        'cameraId': None,
        'microphoneId': None,
        'speakerId': None,
    }
    default_sources = {'version': 1, 'sources': []}
    default_bgm = {
        'version': 1,
        'enabled': False,
        'track': None,
        'volume': 0.5,
        'loop': True,
    }

    for session in StudioSession.objects.all():
        if StudioScene.objects.filter(session_id=session.id).exists():
            continue

        scene = StudioScene.objects.create(
            session_id=session.id,
            name='Scene 1',
            scene_type='CAMERA',
            sort_order=0,
            layout=session.layout or 'CONTAIN',
            graphics_config=session.graphics_config or empty_graphics,
            devices_config=default_devices,
            sources_config=default_sources,
            background_music_config=default_bgm,
        )
        session.active_scene_id = scene.id
        session.save(update_fields=['active_scene_id'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('scenes', '0001_initial'),
        ('studio_sessions', '0004_studiosession_active_scene'),
    ]

    operations = [
        migrations.RunPython(create_default_scenes, noop_reverse),
    ]
