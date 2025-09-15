from __future__ import annotations

from datetime import datetime
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


# Helpful constants
SEX_CHOICES = (
    ("M", "Male"),
    ("F", "Female"),
    ("O", "Other"),
    ("U", "Unknown"),
)


# Patient lifecycle states
class PatientStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    DISCHARGED = "DISCHARGED", "Discharged"
    ARCHIVED = "ARCHIVED", "Archived"


class Patient(models.Model):
    # Core identifiers & demographics
    mrn = models.CharField(max_length=50, blank=True, null=True)

    # Structured name fields (new)
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    suffix = models.CharField(max_length=50, blank=True)

    # Legacy combined name (kept during transition; auto-synced in save())
    name = models.CharField(max_length=200)

    dob = models.DateField("Date of Birth")
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default="U")

    # Clinical/census fields
    location = models.CharField(max_length=100, blank=True)
    diagnosis = models.CharField(max_length=255, blank=True)

    # Admission-day narrative / key summary
    patient_information = models.TextField(
        "Patient Information",
        blank=True,
        help_text="Admission-day narrative or key summary for this patient.",
    )

    admission_date = models.DateField(null=True, blank=True)
    admission_time = models.TimeField(null=True, blank=True)

    # REQUIRED: Attending physician (source of truth for census)
    attending = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,   # don't allow deleting a user who still has patients
        related_name="patients",
        null=False,                 # enforce required in DB
        blank=False,                # enforce required in admin/forms
    )

    # ↓↓↓ LIFECYCLE FIELDS ↓↓↓
    status = models.CharField(
        max_length=16,
        choices=PatientStatus.choices,
        default=PatientStatus.ACTIVE,
        db_index=True,
    )
    discharged_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Keep legacy "name" field in sync for backward compatibility
    def save(self, *args, **kwargs):
        parts = [
            (self.last_name or "").strip(),
            (self.first_name or "").strip(),
        ]
        full = ", ".join([p for p in parts if p])  # "Last, First"
        if self.middle_name:
            full += f" {self.middle_name.strip()}"
        if self.suffix:
            full += f", {self.suffix.strip()}"
        self.name = full.strip()
        super().save(*args, **kwargs)

    # Computed age in full years from DOB (read-only; not stored in DB)
    @property
    def age_years(self):
        if not self.dob:
            return None
        today = timezone.now().date()
        return today.year - self.dob.year - (
            (today.month, today.day) < (self.dob.month, self.dob.day)
        )

    class Meta:
        ordering = ("-admission_date", "-admission_time", "-created_at")
        indexes = [
            models.Index(fields=["mrn"]),
            models.Index(fields=["name"]),
            models.Index(fields=["admission_date", "admission_time"]),
            models.Index(fields=["attending"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.mrn} — {self.name}"

    # Convenience helpers for lifecycle transitions
    def discharge(self, when: datetime | None = None):
        """
        Set status to DISCHARGED and stamp discharged_at once.
        Does not call save(); caller decides when to save.
        """
        when = when or timezone.now()
        self.status = PatientStatus.DISCHARGED
        if self.discharged_at is None:
            self.discharged_at = when

    def archive(self, when: datetime | None = None):
        """
        Set status to ARCHIVED and stamp archived_at once.
        Does not call save(); caller decides when to save.
        """
        when = when or timezone.now()
        self.status = PatientStatus.ARCHIVED
        if self.archived_at is None:
            self.archived_at = when

    @property
    def is_read_only(self) -> bool:
        # Non-active patients should be read-only in most UIs
        return self.status != PatientStatus.ACTIVE


class Signout(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="signouts")
    entry_date = models.DateField()
    text = models.TextField()

    # who created the signout entry
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_signouts",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-entry_date", "-created_at")

    def __str__(self) -> str:
        return f"Signout {self.patient.mrn} @ {self.entry_date}"


class Todo(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="todos")
    text = models.TextField()

    is_completed = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_todos",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("is_completed", "-created_at")
        verbose_name = "To-Do"
        verbose_name_plural = "To-Dos"

    def __str__(self) -> str:
        return f"Todo for {self.patient.mrn}"

    def save(self, *args, **kwargs):
        # Auto-set completed_at when is_completed flips True and timestamp not set
        if self.is_completed and self.completed_at is None:
            self.completed_at = timezone.now()
        super().save(*args, **kwargs)


class OvernightEvent(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="overnight_events")
    description = models.TextField()
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Overnight event for {self.patient.mrn}"


class Assignment(models.Model):
    """
    Optional feature (present but not used for census):
    Could capture historical coverage/shifts per patient.
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="assignments")
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="patient_assignments"
    )
    role = models.CharField(max_length=50, blank=True)  # e.g., "Hospitalist", "Nocturnist", etc.
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-start_date", "-created_at")
        indexes = [
            models.Index(fields=["provider"]),
            models.Index(fields=["start_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.patient.mrn} — {self.provider} ({self.role})"


# -------------------------------
# In-App Notifications (P6-C2)
# -------------------------------
class Notification(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    class Kind(models.TextChoices):
        GENERIC = "generic", "Generic"
        ASSIGNMENT = "assignment", "Patient Assignment"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="User who will see this notification in-app.",
    )
    message = models.TextField(help_text="Notification body shown to the user.")
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)

    # Categorize the notification so we can manage/replace specific kinds (like assignments)
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.GENERIC, db_index=True)

    # Optional association to a patient (lets us deep-link later)
    patient = models.ForeignKey("Patient", on_delete=models.SET_NULL, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    # When the notification becomes visible in the app (used for quiet hours deferral)
    visible_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-visible_at", "-created_at")
        indexes = [
            models.Index(fields=["recipient", "visible_at"]),
            models.Index(fields=["level"]),
            models.Index(fields=["read_at"]),
            models.Index(fields=["kind", "patient", "visible_at"]),  # for dedupe checks
            # PostgreSQL partial index for fast "unread" lookups:
            models.Index(
                fields=["recipient"],
                name="notify_unread_idx",
                condition=Q(read_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.level}/{self.kind}] → {self.recipient} · {self.visible_at:%Y-%m-%d %H:%M}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_read(self, when: datetime | None = None) -> None:
        self.read_at = when or timezone.now()
        self.save(update_fields=["read_at"])

    def mark_unread(self) -> None:
        self.read_at = None
        self.save(update_fields=["read_at"])

    @classmethod
    def push(
        cls,
        *,
        recipient,
        message: str,
        level: str = Level.INFO,
        patient: Patient | None = None,
        visible_at: datetime | None = None,
        kind: str = Kind.GENERIC,
    ) -> "Notification":
        """
        Centralized creator for notifications. In the future, you can also fan-out here
        (e.g., SMS/email) without changing callers.
        """
        return cls.objects.create(
            recipient=recipient,
            message=message,
            level=level,
            patient=patient,
            visible_at=visible_at or timezone.now(),
            kind=kind,
        )


# -------------------------------
# Patient Watch (private lists)
# -------------------------------
class PatientWatch(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watched_patients",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="watchers",
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "patient"],
                name="uniq_active_watch_per_user_patient",
                condition=Q(archived_at__isnull=True),
            )
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["patient", "created_at"]),
        ]

    def __str__(self):
        status = "active" if self.archived_at is None else "archived"
        return f"{self.user} → {self.patient} ({status})"


# -------------------------------
# Audit Trail (P6-C3) — Step 1
# -------------------------------
class AuditLog(models.Model):
    class Event(models.TextChoices):
        ATTENDING_CHANGED = "ATTENDING_CHANGED", "Attending changed"

    event = models.CharField(max_length=40, choices=Event.choices)
    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="audit_logs")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="patient_audit_logs",
    )
    old_attending = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_old_attending",
    )
    new_attending = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_new_attending",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["event", "created_at"]),
            models.Index(fields=["patient", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.get_event_display()} — {self.patient}"
