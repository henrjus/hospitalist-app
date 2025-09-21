# patients/apps.py
from django.apps import AppConfig

class PatientsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "patients"

    def ready(self):
        # Import signal handlers so Django connects them
        from . import audit  # noqa: F401
