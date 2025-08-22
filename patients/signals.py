from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import Patient, AuditLog, PatientWatch


# ---------- helpers ----------
def _is_placeholder(user) -> bool:
    # Treat the special placeholder user as "do nothing" for watches
    return bool(user and getattr(user, "username", None) == "to_be_assigned")


def _ensure_active_watch(user, patient):
    """
    Ensure the (user, patient) watch exists and is active.
    - If an active watch exists -> do nothing
    - If an archived watch exists -> reactivate (set archived_at=None)
    - Else -> create new
    """
    if not user or _is_placeholder(user):
        return

    qs = PatientWatch.objects.filter(user=user, patient=patient)
    if qs.filter(archived_at__isnull=True).exists():
        return
    if qs.filter(archived_at__isnull=False).exists():
        qs.update(archived_at=None)
        return
    PatientWatch.objects.create(user=user, patient=patient, note="")
    return


# ---------- attending change tracking ----------
@receiver(pre_save, sender=Patient)
def _store_previous_attending(sender, instance: Patient, **kwargs):
    # Cache previous attending id for comparison in post_save
    if instance.pk:
        try:
            old = sender.objects.only("attending_id").get(pk=instance.pk)
            instance._prev_attending_id = old.attending_id
        except sender.DoesNotExist:
            instance._prev_attending_id = None
    else:
        instance._prev_attending_id = None


@receiver(post_save, sender=Patient)
def _post_save_patient(sender, instance: Patient, created, **kwargs):
    """
    - On create: auto-add to watchlist for current attending (if not placeholder)
    - On update: if attending changed, write AuditLog and auto-add for NEW attending
    """
    User = get_user_model()

    if created:
        # Auto-watch on initial creation (if attending is a real user)
        new_user = instance.attending
        if new_user:
            _ensure_active_watch(new_user, instance)
        return

    prev_id = getattr(instance, "_prev_attending_id", None)
    curr_id = instance.attending_id
    if prev_id == curr_id:
        return

    old_user = User.objects.filter(pk=prev_id).first() if prev_id else None
    new_user = User.objects.filter(pk=curr_id).first() if curr_id else None

    # 1) Audit trail
    AuditLog.objects.create(
        event=AuditLog.Event.ATTENDING_CHANGED,
        patient=instance,
        changed_by=getattr(instance, "_changed_by_user", None),  # set by admin.save_model
        old_attending=old_user,
        new_attending=new_user,
        created_at=timezone.now(),
    )

    # 2) Auto-add NEW attending to their watchlist
    _ensure_active_watch(new_user, instance)
