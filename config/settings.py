"""
Django settings for the mini-streaming-studio compositor backend.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv(
    'SECRET_KEY',
    'django-insecure-bc$_nv*)-!=4xm+f#!ztdhv9v1$x60%a-wq3f&8dq!335yqj4r',
)

DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
    if host.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'apps.sessions.apps.StudioSessionsConfig',
    'apps.compositor.apps.CompositorConfig',
    'apps.layouts.apps.LayoutsConfig',
    'apps.recording.apps.RecordingConfig',
    'apps.streaming.apps.StreamingConfig',
    'apps.sources.apps.SourcesConfig',
    'apps.graphics.apps.GraphicsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'apps.compositor': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'apps.recording': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'apps.graphics': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

# --- REST Framework ---
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
    ],
}

# --- CORS (studio-frontend will connect in a later phase) ---
CORS_ALLOWED_ORIGINS = [
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]
CORS_ALLOW_CREDENTIALS = True

# --- Studio / compositor settings ---
MEDIASOUP_API_URL = os.getenv('MEDIASOUP_API_URL', 'http://localhost:4443')
MEDIASOUP_ORIGIN = os.getenv('MEDIASOUP_ORIGIN', 'http://localhost:4443')
MEDIASOUP_WS_URL = os.getenv('MEDIASOUP_WS_URL', 'ws://localhost:4443')
STUDIO_FRONTEND_URL = os.getenv('STUDIO_FRONTEND_URL', 'http://localhost:5173')
COMPOSITOR_RTP_HOST = os.getenv('COMPOSITOR_RTP_HOST', '127.0.0.1')
COMPOSITOR_RTP_PORT_MIN = int(os.getenv('COMPOSITOR_RTP_PORT_MIN', '50000'))
COMPOSITOR_RTP_PORT_MAX = int(os.getenv('COMPOSITOR_RTP_PORT_MAX', '50999'))
PRODUCER_POLL_INTERVAL = float(os.getenv('PRODUCER_POLL_INTERVAL', '2'))

CANVAS_WIDTH = int(os.getenv('CANVAS_WIDTH', '1920'))
CANVAS_HEIGHT = int(os.getenv('CANVAS_HEIGHT', '1080'))
CANVAS_FPS = int(os.getenv('CANVAS_FPS', '30'))

# Video mix backend: cpu | gl | cuda | auto (cuda → gl → cpu)
_COMPOSITOR_VIDEO_BACKEND = os.getenv('COMPOSITOR_VIDEO_BACKEND', 'auto').strip().lower()
if _COMPOSITOR_VIDEO_BACKEND not in ('cpu', 'gl', 'cuda', 'auto'):
    raise ValueError(
        f'Invalid COMPOSITOR_VIDEO_BACKEND={_COMPOSITOR_VIDEO_BACKEND!r}; '
        "expected 'cpu', 'gl', 'cuda', or 'auto'"
    )
COMPOSITOR_VIDEO_BACKEND = _COMPOSITOR_VIDEO_BACKEND
COMPOSITOR_CUDA_DEVICE_ID = int(os.getenv('COMPOSITOR_CUDA_DEVICE_ID', '-1'))
COMPOSITOR_DISABLE_BACKGROUND = os.getenv(
    'COMPOSITOR_DISABLE_BACKGROUND',
    'false',
).lower() in ('true', '1', 'yes')

RECORDINGS_DIR = Path(os.getenv('RECORDINGS_DIR', str(BASE_DIR / 'recordings')))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
RECORDING_VIDEO_BITRATE = int(os.getenv('RECORDING_VIDEO_BITRATE', '4000000'))
RECORDING_AUDIO_BITRATE = int(os.getenv('RECORDING_AUDIO_BITRATE', '128000'))
RECORDING_EOS_TIMEOUT_SEC = float(os.getenv('RECORDING_EOS_TIMEOUT_SEC', '15'))

STREAMING_HLS_DIR = Path(os.getenv('STREAMING_HLS_DIR', str(BASE_DIR / 'streams' / 'hls')))
STREAMING_HLS_DIR.mkdir(parents=True, exist_ok=True)
STREAMING_VIDEO_BITRATE = int(os.getenv('STREAMING_VIDEO_BITRATE', '2500000'))
STREAMING_AUDIO_BITRATE = int(os.getenv('STREAMING_AUDIO_BITRATE', '128000'))
STREAMING_EOS_TIMEOUT_SEC = float(os.getenv('STREAMING_EOS_TIMEOUT_SEC', '5'))
DEFAULT_RTMP_URL = os.getenv('DEFAULT_RTMP_URL', '')

WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')
WEBHOOK_TIMEOUT_SEC = float(os.getenv('WEBHOOK_TIMEOUT_SEC', '5'))

STREAMING_RTMP_MAX_RECONNECT_ATTEMPTS = int(
    os.getenv('STREAMING_RTMP_MAX_RECONNECT_ATTEMPTS', '5')
)
STREAMING_RTMP_RECONNECT_DELAY_SEC = float(
    os.getenv('STREAMING_RTMP_RECONNECT_DELAY_SEC', '3')
)
GRACEFUL_SHUTDOWN_TIMEOUT_SEC = float(os.getenv('GRACEFUL_SHUTDOWN_TIMEOUT_SEC', '30'))
