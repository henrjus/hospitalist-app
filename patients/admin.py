from datetime import datetime, time
from django.contrib import admin
from django.utils import timezone

from .models import Patient, Signout, Todo, OvernightEvent


# ---------------------------
# Patient Admin
# ---------------------------

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    """
    Uses your exact fields:
      - attending_provider
      - admission_date (DateField)
      - admission_time (TimeField, optional)
    """
    list_display = (
        "mrn",
        "name",
        "age_years",
        "los_days",
        "location",
        "attending_provider",
        "admit_col",
    )
    list_display_links = ("mrn", "name")

    # Safe, real fields:
    # (added admission_date to filters per 5.5.F3)
    list_filter = ("sex", "location", "attending_provider", "admission_date")

    # Search MRN/Name/Diagnosis:
    search_fields = ("mrn", "name", "diagnosis")

    # Order by date then time (both real fields)
    ordering = ("-admission_date", "-admission_time")

    # Nice calendar drill-down on the date field
    date_hierarchy = "admission_date"

    # If you have these (you do on Patient), surface them read-only in forms
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Age")
    def age_years(self, obj: Patient):
        if not obj.dob:
            return ""
        today = timezone.localdate()
        years = today.year - obj.dob.year - (
            (today.month, today.day) < (obj.dob.month, obj.dob.day)
        )
        return years

    def _admit_dt(self, obj: Patient):
        """
        Build a datetime from (admission_date + optional admission_time).
        If time is missing, assume 12:00 for display purposes.
        """
        if not obj.admission_date:
            return None
        t = obj.admission_time or time(12, 0)
        try:
            # Attach current timezone for consistent display/LOS math
            return timezone.make_aware(
                datetime.combine(obj.admission_date, t),
                timezone.get_current_timezone(),
            )
        except Exception:
            # Fallback naive datetime if timezone not configured
            return datetime.combine(obj.admission_date, t)

    @admin.display(description="LOS (d)")
    def los_days(self, obj: Patient):
        if not obj.admission_date:
            return ""
        # LOS as whole days based on dates → robust even if time missing
        return (timezone.localdate() - obj.admission_date).days

    @admin.display(description="Admit")
    def admit_col(self, obj: Patient):
        dt = self._admit_dt(obj)
        return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


# ---------------------------
# Signout Admin
# ---------------------------

@admin.register(Signout)
class SignoutAdmin(admin.ModelAdmin):
    list_display = ("patient", "entry_date", "created_by", "created_at", "text")
    search_fields = ("patient__name", "patient__mrn", "text")
    date_hierarchy = "entry_date"
    readonly_fields = tuple(f for f in ("created_at", "updated_at") if hasattr(Signout, f))

    # Allow editing text directly in the list view
    list_editable = ("text",)
    list_display_links = ("patient",)


# ---------------------------
# Todo Admin
# ---------------------------

@admin.register(Todo)
class TodoAdmin(admin.ModelAdmin):
    list_display = ("patient", "text", "is_completed", "expires_at", "created_at")
    # Added expires_at filter per 5.5.F3
    list_filter = ("is_completed", "expires_at")
    search_fields = ("patient__name", "patient__mrn", "text")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")

    # Make completion editable right from the list page
    list_editable = ("is_completed",)

    # Keep the first column as the click-through link
    list_display_links = ("patient",)

    # Bulk actions
    actions = ("mark_completed", "mark_not_completed")

    @admin.action(description="Mark selected todos as completed")
    def mark_completed(self, request, queryset):
        queryset.update(is_completed=True, completed_at=timezone.now())

    @admin.action(description="Mark selected todos as NOT completed")
    def mark_not_completed(self, request, queryset):
        queryset.update(is_completed=False, completed_at=None)


# ---------------------------
# OvernightEvent Admin
# ---------------------------

@admin.register(OvernightEvent)
class OvernightEventAdmin(admin.ModelAdmin):
    list_display = ("patient", "description", "created_at", "resolved")
    # Added created_at filter per 5.5.F3
    list_filter = ("resolved", "created_at")
    search_fields = ("patient__name", "patient__mrn", "description")
    date_hierarchy = "created_at"

    # Allow editing description & resolved inline
    list_editable = ("description", "resolved")
    list_display_links = ("patient",)
