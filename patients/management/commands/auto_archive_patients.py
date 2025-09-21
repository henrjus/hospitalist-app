from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from patients.models import Patient, PatientStatus


class Command(BaseCommand):
    help = "Auto-archive patients who have been discharged longer than the grace period."

    def handle(self, *args, **options):
        now = timezone.now()
        grace_days = getattr(settings, "PATIENT_DISCHARGE_GRACE_DAYS", 7)
        cutoff = now - timedelta(days=grace_days)

        qs = Patient.objects.filter(
            status=PatientStatus.DISCHARGED,
            discharged_at__lte=cutoff,
        )

        count = qs.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No patients to archive."))
            return

        qs.update(status=PatientStatus.ARCHIVED, archived_at=now)
        self.stdout.write(self.style.SUCCESS(f"Archived {count} patient(s)."))
