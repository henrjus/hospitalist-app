from django.db import models

class Patient(models.Model):
    SEX_CHOICES = [
        ('F', 'Female'),
        ('M', 'Male'),
        ('O', 'Other'),
        ('U', 'Unknown'),
    ]

    # Core demographics
    mrn = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    dob = models.DateField(verbose_name="Date of Birth", null=True, blank=True)
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default='U')

    diagnosis = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=120, blank=True)  # e.g., "4W-12B"
    attending_provider = models.CharField(max_length=120, blank=True)

    admission_date = models.DateField(null=True, blank=True)
    admission_time = models.TimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["mrn"]),
            models.Index(fields=["name"]),
            models.Index(fields=["location"]),
        ]

    def __str__(self):
        return f"{self.name} (MRN {self.mrn})"

from django.conf import settings

class Signout(models.Model):
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='signouts')
    entry_date = models.DateField()  # the day this signout applies to
    text = models.TextField()

    # metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_signouts'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['entry_date', 'created_at']
        indexes = [
            models.Index(fields=['entry_date']),
        ]
        unique_together = [('patient', 'entry_date', 'created_at')]
        verbose_name = 'Signout entry'
        verbose_name_plural = 'Signout entries'

    def __str__(self):
        who = f" by {self.created_by}" if self.created_by else ""
        return f"{self.patient.name} — {self.entry_date}{who}"

from django.utils import timezone

class Todo(models.Model):
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='todos')
    text = models.CharField(max_length=300)
    # status & timing
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_todos'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['is_completed', '-created_at']
        indexes = [
            models.Index(fields=['is_completed']),
            models.Index(fields=['expires_at']),
        ]
        verbose_name = 'To‑do'
        verbose_name_plural = 'To‑dos'

    def __str__(self):
        status = "✓" if self.is_completed else "•"
        return f"{status} {self.patient.name}: {self.text[:40]}"

    def mark_completed(self):
        self.is_completed = True
        if not self.completed_at:
            self.completed_at = timezone.now()

class OvernightEvent(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="overnight_events")
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.patient.name} - {self.description[:30]}"


