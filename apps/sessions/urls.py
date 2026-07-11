from django.urls import path

from apps.recording.views import (
    SessionRecordingListView,
    SessionRecordingStartView,
    SessionRecordingStopView,
)
from apps.streaming.views import (
    SessionStreamListView,
    SessionStreamStartView,
    SessionStreamStopView,
)
from apps.sessions.views import (
    HealthView,
    MetricsView,
    SessionDetailView,
    SessionIngestView,
    SessionLayoutView,
    SessionListCreateView,
    ValidateInviteView,
)

urlpatterns = [
    path('health/', HealthView.as_view(), name='health'),
    path('metrics/', MetricsView.as_view(), name='metrics'),
    path('sessions/', SessionListCreateView.as_view(), name='session-list-create'),
    path('sessions/<uuid:session_id>/', SessionDetailView.as_view(), name='session-detail'),
    path(
        'sessions/<uuid:session_id>/layout/',
        SessionLayoutView.as_view(),
        name='session-layout',
    ),
    path(
        'sessions/<uuid:session_id>/validate-invite/',
        ValidateInviteView.as_view(),
        name='session-validate-invite',
    ),
    path(
        'sessions/<uuid:session_id>/ingest/',
        SessionIngestView.as_view(),
        name='session-ingest',
    ),
    path(
        'sessions/<uuid:session_id>/recordings/',
        SessionRecordingListView.as_view(),
        name='session-recording-list',
    ),
    path(
        'sessions/<uuid:session_id>/recordings/start/',
        SessionRecordingStartView.as_view(),
        name='session-recording-start',
    ),
    path(
        'sessions/<uuid:session_id>/recordings/stop/',
        SessionRecordingStopView.as_view(),
        name='session-recording-stop',
    ),
    path(
        'sessions/<uuid:session_id>/streams/',
        SessionStreamListView.as_view(),
        name='session-stream-list',
    ),
    path(
        'sessions/<uuid:session_id>/streams/start/',
        SessionStreamStartView.as_view(),
        name='session-stream-start',
    ),
    path(
        'sessions/<uuid:session_id>/streams/stop/',
        SessionStreamStopView.as_view(),
        name='session-stream-stop',
    ),
]
