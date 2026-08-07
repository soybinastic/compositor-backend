"""Default config blobs for scene forward-compat fields."""

DEFAULT_DEVICES_CONFIG: dict = {
    'cameraId': None,
    'cameraLabel': None,
    'microphoneId': None,
    'microphoneLabel': None,
    'speakerId': None,
}

DEFAULT_SOURCES_CONFIG: dict = {
    'version': 2,
    'items': [],
    'sources': [],
    'assignments': {},
}

DEFAULT_BACKGROUND_MUSIC_CONFIG: dict = {
    'version': 1,
    'enabled': False,
    'track': None,
    'volume': 0.5,
    'loop': True,
}
