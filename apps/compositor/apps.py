from django.apps import AppConfig


class CompositorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.compositor'
    verbose_name = 'Compositor'

    def ready(self) -> None:
        import sys

        if 'test' in sys.argv:
            return

        # Avoid registering handlers in Django autoreloader parent process.
        if 'runserver' in sys.argv and __import__('os').environ.get('RUN_MAIN') != 'true':
            return

        from apps.compositor.shutdown import register_shutdown_handlers

        register_shutdown_handlers()
