from datetime import date, datetime, time
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.sites import NotRegistered
from django.contrib.admin import SimpleListFilter
from django.contrib.admin.helpers import ActionForm
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Patient, PatientStatus, Signout, Todo, OvernightEvent, Assignment

User = get_user_model()

# ========== Helpers ==========

def _calc_age(dob: date | None) -> int | None:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

def _calc_los(admit_date: date | None, admit_time: time | None) -> int | None:
    if not admit_date:
        return None
    if admit_time:
        admit_dt = datetime.combine(admit_date, admit_time, tzinfo=timezone.get_current_timezone())
    else:
        admit_dt = datetime.combine(admit_date, datetime.min.time(), tzinfo=timezone.get_current_timezone())
    return (timezone.now() - admit_dt).days

# ========== Inlines ==========

class SignoutInline(admin.StackedInline):
    model = Signout
    extra = 0
    fields = ("entry_date", "text", "created_by")
    readonly_fields = ("created_by",)
    show_change_link = True
    ordering = ("entry_date",)   # Oldest first
    # Default expanded (no 'collapse' class)

    def formfield_for_dbfield(self, db_field, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, **kwargs)
        if db_field.name == "text":
            formfield.widget.attrs["rows"] = 4
            formfield.widget.attrs["style"] = "width: 95%;"
        return formfield

class TodoInline(admin.TabularInline):
    model = Todo
    extra = 0
    fields = ("text", "is_completed", "expires_at", "completed_at", "created_by")
    readonly_fields = ("completed_at", "created_by")
    show_change_link = True

class OvernightEventInline(admin.TabularInline):
    model = OvernightEvent
    extra = 0
    fields = ("description", "resolved", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True

class AssignmentInline(admin.TabularInline):
    model = Assignment
    extra = 1
    fields = ("provider", "role", "start_date", "end_date")
    show_change_link = True

    # Hide add/change/delete related buttons on the provider FK inside the inline
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        form = formset.form
        if "provider" in form.base_fields:
            w = form.base_fields["provider"].widget
            for attr in ("can_add_related", "can_change_related", "can_delete_related"):
                if hasattr(w, attr):
                    setattr(w, attr, False)
        return formset

# ========== Custom Filters ==========

class StatusListFilter(SimpleListFilter):
    title = _("Status")
    parameter_name = "status__exact"

    def lookups(self, request, model_admin):
        # Only actual statuses here; rely on Django's built-in "All" link at the top
        return [
            (PatientStatus.ACTIVE, _("Active")),
            (PatientStatus.DISCHARGED, _("Discharged")),
            (PatientStatus.ARCHIVED, _("Archived")),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value in {PatientStatus.ACTIVE, PatientStatus.DISCHARGED, PatientStatus.ARCHIVED}:
            return queryset.filter(status=value)
        # value is None => built-in "All" selected => no status filter applied
        return queryset

# ========== Patients Admin (with Attending FK + Bulk Action + Lifecycle) ==========

class SetAttendingActionForm(ActionForm):
    attending = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by("last_name", "first_name", "username"),
        required=False,
        label="Assign attending"
    )
    clear_attending = forms.BooleanField(
        required=False,
        label="Set to TO BE ASSIGNED"
    )

# --- PatientAdmin form to enlarge Patient Information textarea ---
class PatientAdminForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = "__all__"
        widgets = {
            "patient_information": forms.Textarea(attrs={"rows": 8}),
        }

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientAdminForm

    list_display = (
        "mrn",
        "name",
        "age_years",
        "los_days",
        "location",
        "attending",       # FK to User (source of truth)
        "admit_display",
        "status",          # lifecycle
        "discharged_at",
        "archived_at",
    )
    list_filter = (
        "sex",
        "location",
        "attending",
        "admission_date",
        StatusListFilter,  # lifecycle filter
    )
    search_fields = (
        "mrn",
        "name",
        "diagnosis",
        "attending__username",
        "attending__first_name",
        "attending__last_name",
    )
    list_select_related = ("attending",)
    date_hierarchy = "admission_date"
    ordering = ("-admission_date", "-admission_time")
    autocomplete_fields = ("attending",)

    # ✅ Signouts inline first
    inlines = [SignoutInline, AssignmentInline, TodoInline, OvernightEventInline]

    # ✅ Patient Information fieldset last
    fieldsets = (
        ("Identifiers", {
            "fields": ("mrn", "name", "dob", "sex")
        }),
        ("Clinical / Census", {
            "fields": ("location", "diagnosis", "admission_date", "admission_time", "attending")
        }),
        ("Lifecycle", {
            "fields": ("status", "discharged_at", "archived_at")
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
        ("Patient Information", {
            "fields": ("patient_information",),
        }),
    )

    # --- IMPORTANT: Non-editable fields must be readonly if in fieldsets ---
    readonly_fields = ("created_at", "updated_at", "discharged_at", "archived_at")

    # Default to Active only on first arrival (no referrer).
    def changelist_view(self, request, extra_context=None):
        if "status__exact" not in request.GET:
            ref = request.META.get("HTTP_REFERER", "")
            if not ref:  # first visit => default to Active
                q = request.GET.copy()
                q["status__exact"] = PatientStatus.ACTIVE
                request.GET = q
                request.META["QUERY_STRING"] = q.urlencode()
        return super().changelist_view(request, extra_context=extra_context)

    # ---------- Lifecycle Admin Actions ----------

    action_form = SetAttendingActionForm
    actions = [
        "bulk_set_or_clear_attending",
        "mark_active",
        "discharge_now",
        "archive_now",
    ]

    @admin.action(description="Mark Active (clear discharge/archive timestamps)")
    def mark_active(self, request, queryset):
        updated = queryset.update(
            status=PatientStatus.ACTIVE,
            discharged_at=None,
            archived_at=None,
        )
        self.message_user(request, f"Marked {updated} patient(s) as ACTIVE.", level=messages.SUCCESS)

    @admin.action(description="Discharge now (sets discharged_at=now)")
    def discharge_now(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(
            status=PatientStatus.DISCHARGED,
            discharged_at=now,
        )
        grace = getattr(settings, "PATIENT_DISCHARGE_GRACE_DAYS", 7)
        self.message_user(
            request,
            f"Discharged {updated} patient(s). They will be eligible for auto-archive after {grace} day(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Archive now (sets archived_at=now)")
    def archive_now(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(
            status=PatientStatus.ARCHIVED,
            archived_at=now,
        )
        self.message_user(request, f"Archived {updated} patient(s).", level=messages.SUCCESS)

    # Existing Attending bulk action
    @admin.action(description="Set/Clear Attending for selected patients")
    def bulk_set_or_clear_attending(self, request, queryset):
        attending_id = request.POST.get("attending")
        clear = request.POST.get("clear_attending")

        # Resolve placeholder user
        try:
            tba_user = User.objects.get(username="to_be_assigned")
        except User.DoesNotExist:
            self.message_user(
                request,
                "Placeholder user 'to_be_assigned' was not found. Please run migrations again.",
                level=messages.ERROR,
            )
            return

        if clear:
            updated = queryset.update(attending=tba_user)
            self.message_user(
                request,
                f"Set Attending to TO BE ASSIGNED on {updated} patient(s).",
                level=messages.SUCCESS,
            )
            return

        if attending_id:
            try:
                user = User.objects.get(pk=attending_id)
            except User.DoesNotExist:
                self.message_user(request, "Selected Attending user not found.", level=messages.ERROR)
                return

            updated = queryset.update(attending=user)
            display_name = user.get_full_name() or user.username
            self.message_user(
                request,
                f"Set Attending to {display_name} on {updated} patient(s).",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "Choose an Attending or check 'Set to TO BE ASSIGNED' before running the action.",
                level=messages.WARNING,
            )

    # ---------- Read-only behavior for non-Active patients ----------

    def get_readonly_fields(self, request, obj=None):
        """
        Make patients read-only when status != ACTIVE.
        Also always lock computed display columns.
        """
        ro = list(super().get_readonly_fields(request, obj))
        # Always include computed display-only fields
        ro += ["age_years", "los_days", "admit_display"]
        if obj and obj.status != PatientStatus.ACTIVE:
            # Mark all concrete model fields as read-only for non-active patients
            ro += [f.attname for f in obj._meta.concrete_fields]
        return tuple(sorted(set(ro)))

    def has_delete_permission(self, request, obj=None):
        """
        Prevent deleting Discharged/Archived patients from Admin.
        """
        if obj and obj.status != PatientStatus.ACTIVE:
            return False
        return super().has_delete_permission(request, obj=obj)

    def get_inline_instances(self, request, obj=None):
        """
        Hide inlines when the patient is not Active (to enforce read-only).
        """
        if obj and obj.status != PatientStatus.ACTIVE:
            return []
        return super().get_inline_instances(request, obj)

    # ---------- Misc formatting ----------

    def get_changeform_initial_data(self, request):
        """Default Attending to the placeholder when adding a new patient."""
        initial = super().get_changeform_initial_data(request)
        try:
            tba_user = User.objects.get(username="to_be_assigned")
            initial.setdefault("attending", tba_user.pk)
        except User.DoesNotExist:
            pass
        return initial

    def get_form(self, request, obj=None, **kwargs):
        """
        After Django wraps the widgets, disable the add/change/delete related
        buttons for the 'attending' FK to avoid confusing user management UI.
        """
        form = super().get_form(request, obj, **kwargs)
        if "attending" in form.base_fields:
            w = form.base_fields["attending"].widget
            for attr in ("can_add_related", "can_change_related", "can_delete_related"):
                if hasattr(w, attr):
                    setattr(w, attr, False)
        return form

    @admin.display(description="Age", ordering="dob")
    def age_years(self, obj: Patient):
        return _calc_age(obj.dob) or "—"

    @admin.display(description="LOS (d)")
    def los_days(self, obj: Patient):
        return _calc_los(obj.admission_date, obj.admission_time) or "—"

    @admin.display(description="Admit", ordering="admission_date")
    def admit_display(self, obj: Patient):
        if not obj.admission_date:
            return "—"
        if obj.admission_time:
            return f"{obj.admission_date} {obj.admission_time.strftime('%H:%M')}"
        return str(obj.admission_date)

# ========== Other Models ==========

@admin.register(Signout)
class SignoutAdmin(admin.ModelAdmin):
    list_display = ("patient", "entry_date", "created_by", "created_at")
    list_filter = ("entry_date", "created_by")
    search_fields = ("patient__mrn", "patient__name", "text")
    date_hierarchy = "entry_date"
    ordering = ("-entry_date", "-created_at")

@admin.register(Todo)
class TodoAdmin(admin.ModelAdmin):
    list_display = ("patient", "text_short", "is_completed", "expires_at", "created_by", "created_at")
    list_filter = ("is_completed", "expires_at", "created_by")
    search_fields = ("patient__mrn", "patient__name", "text")
    ordering = ("is_completed", "-created_at")

    @admin.display(description="Text")
    def text_short(self, obj: Todo):
        return obj.text if len(obj.text) <= 60 else f"{obj.text[:57]}..."

@admin.register(OvernightEvent)
class OvernightEventAdmin(admin.ModelAdmin):
    list_display = ("patient", "desc_short", "resolved", "created_at")
    list_filter = ("resolved", "created_at")
    search_fields = ("patient__mrn", "patient__name", "description")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    @admin.display(description="Description")
    def desc_short(self, obj: OvernightEvent):
        return obj.description if len(obj.description) <= 60 else f"{obj.description[:57]}..."

# ========== Assignments Admin (single registration) ==========

class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "provider", "role", "start_date", "end_date", "created_at")
    list_filter = ("role", "provider", "start_date")
    search_fields = (
        "patient__mrn",
        "patient__name",
        "provider__username",
        "provider__first_name",
        "provider__last_name",
    )
    ordering = ("-start_date", "-created_at")

# Ensure only one registration of Assignment, even if this file reloads.
try:
    admin.site.unregister(Assignment)
except NotRegistered:
    pass
admin.site.register(Assignment, AssignmentAdmin)
