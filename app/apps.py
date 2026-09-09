from django.apps import AppConfig


class AppConfig(AppConfig):
    name = 'app'

    def ready(self):
        # Enregistre les signaux (invalidation du cache du dashboard).
        from . import signals  # noqa: F401
