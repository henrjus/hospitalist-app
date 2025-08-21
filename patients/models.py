from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


# Helpful constants
SEX_CHOICES = (
    ("M", "Male"),
    ("F", "Female"),
    ("O", "Other"),
    ("U", "Unknown"),
)


class Patient(models.Model):
    # Core identifiers & demographics
    mrn = models.CharField("MRN", max_length=32, unique=True)
    name = models.CharField(max_length=200)
    dob = models.DateField("Date of Birth", null=True, blank=True)
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default="U")

    # Clinical/census fields
    location = models.CharField(max_length=100, blank=True)
    diagnosis = models.CharField(max_length=255, blank=True)

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

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-admission_date", "-admission_time", "-created_at")
        indexes = [
            models.Index(fields=["mrn"]),
            models.Index(fields=["name"]),
            models.Index(fields=["admission_date", "admission_time"]),
            models.Index(fields=["attending"]),
        ]

    def __str__(self) -> str:
        return f"{self.mrn} — {self.name}"


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
        verbose_name = "To‑Do"
        verbose_name_plural = "To‑Dos"

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
