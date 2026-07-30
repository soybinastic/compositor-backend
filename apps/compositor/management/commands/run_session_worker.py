import sys

from django.core.management.base import BaseCommand

from apps.compositor.worker_manager.redis_ipc import get_redis_client
from apps.compositor.worker_manager.worker_process import run_session_worker
from core.worker_events import mark_worker_process


class Command(BaseCommand):
    help = 'Run a single session worker subprocess (GStreamer + GLib executor).'

    def add_arguments(self, parser):
        parser.add_argument('session_id', help='Studio session UUID')

    def handle(self, *args, **options):
        mark_worker_process()
        session_id = options['session_id']
        exit_code = run_session_worker(
            session_id,
            redis_client=get_redis_client(),
        )
        if exit_code:
            sys.exit(exit_code)
