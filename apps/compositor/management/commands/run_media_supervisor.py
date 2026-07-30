from django.core.management.base import BaseCommand

from apps.compositor.worker_manager.media_supervisor import MediaSupervisor


class Command(BaseCommand):
    help = 'Run the Media Supervisor (spawns session worker subprocesses).'

    def handle(self, *args, **options):
        supervisor = MediaSupervisor()
        try:
            supervisor.run_forever()
        except KeyboardInterrupt:
            self.stdout.write('Shutting down Media Supervisor...')
            supervisor.shutdown_all()
