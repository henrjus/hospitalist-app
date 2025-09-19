from datetime import date, datetime, time, timedelta

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.contrib.admin.helpers import ActionForm
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django.urls import path
from django.shortcuts import redirect
from django.utils.html import format_html


from .models import (
    Patient, PatientStatus, Signout, Todo, OvernightEvent,
    Assignment, Notification, AuditLog, PatientWatch
)

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

# Quiet hours config (defaults: 16 → 7)
QUIET_START_HOUR = getattr(settings, "NOTIFY_QUIET_START_HOUR", 16)  # 4 PM local
QUIET_END_HOUR = getattr(settings, "NOTIFY_QUIET_END_HOUR", 7)       # 7 AM local

def _is_quiet_hours(now: timezone.datetime) -> bool:
    """Return True if local hour is within quiet window."""
    local_now = timezone.localtime(now)
    h = local_now.hour
    start = QUIET_START_HOUR
    end = QUIET_END_HOUR
    if start == end:
        return False
    if start < end:
        # same-day window (e.g., 9→17)
        return start <= h < end
    # overnight window (e.g., 16→7)
    return h >= start or h < end

def _next_visible_time(now: timezone.datetime) -> timezone.datetime:
    """
    If within quiet hours, return the next window end today/tomorrow at end hour.
    Otherwise return 'now'.
    """
    if not _is_quiet_hours(now):
        return now
    local_now = timezone.localtime(now)
    tz = timezone.get_current_timezone()
    deliver_date = local_now.date() if local_now.hour < QUIET_END_HOUR else (local_now.date() + timedelta(days=1))
    naive = datetime.combine(deliver_date, time(hour=QUIET_END_HOUR, minute=0))
    return timezone.make_aware(naive, tz)

# ========== Inlines ==========

class SignoutInline(admin.StackedInline):
    model = Signout
    extra = 0
    fields = ("entry_date", "text", "created_by")
    readonly_fields = ("created_by",)
    show_change_link = True
    ordering = ("entry_date",)   # Oldest first

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
        return [
            (PatientStatus.ACTIVE, _("Active")),
            (PatientStatus.DISCHARGED, _("Discharged")),
            (PatientStatus.ARCHIVED, _("Archived")),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value in {PatientStatus.ACTIVE, PatientStatus.DISCHARGED, PatientStatus.ARCHIVED}:
            return queryset.filter(status=value)
        return queryset

class MyWatchlistFilter(SimpleListFilter):
    parameter_name = "my_watch"

    def __init__(self, request, *args, **kwargs):
        # keep request so we can compute per-user counts and title
        self.request = request
        super().__init__(request, *args, **kwargs)

    @property
    def title(self):
        # Sidebar header shows total active items on *my* watchlist
        from .models import PatientWatch
        total = PatientWatch.objects.filter(
            user=self.request.user,
            archived_at__isnull=True,
        ).count()
        return f"My watchlist ({total})"

    def lookups(self, request, model_admin):
        """
        Show counts in the option labels, scoped to the current list view
        (respects other active filters/search since we use model_admin.get_queryset).
        """
        base_qs = model_admin.get_queryset(request)

        yes_count = (
            base_qs.filter(
                watchers__user=request.user,
                watchers__archived_at__isnull=True,
            )
            .distinct()
            .count()
        )
        no_count = (
            base_qs.exclude(
                watchers__user=request.user,
                watchers__archived_at__isnull=True,
            )
            .distinct()
            .count()
        )

        return [
            ("yes", f"Yes ({yes_count})"),
            ("no", f"No ({no_count})"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(
                watchers__user=request.user,
                watchers__archived_at__isnull=True,
            )
        if self.value() == "no":
            return queryset.exclude(
                watchers__user=request.user,
                watchers__archived_at__isnull=True,
            )
        return queryset


# ========== PatientWatch Admin & Actions ==========

@admin.register(PatientWatch)
class PatientWatchAdmin(admin.ModelAdmin):
    list_display = ("patient", "user", "note", "created_at", "archived_at")
    list_filter = ("user", ("archived_at", admin.EmptyFieldListFilter))
    search_fields = ("patient__mrn", "patient__name", "user__username", "note")
    ordering = ("-created_at",)

@admin.action(description="Add to MY watchlist")
def add_to_my_watchlist(modeladmin, request, queryset):
    created, reactivated, skipped = 0, 0, 0
    for patient in queryset:
        if PatientWatch.objects.filter(user=request.user, patient=patient, archived_at__isnull=True).exists():
            skipped += 1
            continue
        if PatientWatch.objects.filter(user=request.user, patient=patient, archived_at__isnull=False).exists():
            PatientWatch.objects.filter(
                user=request.user, patient=patient, archived_at__isnull=False
            ).update(archived_at=None)
            reactivated += 1
            continue
        PatientWatch.objects.create(user=request.user, patient=patient, note="")
        created += 1

    modeladmin.message_user(
        request,
        f"Watchlist updated — created: {created}, reactivated: {reactivated}, skipped (already active): {skipped}.",
        level=messages.SUCCESS,
    )

@admin.action(description="Remove from MY watchlist")
def remove_from_my_watchlist(modeladmin, request, queryset):
    updated = (
        PatientWatch.objects
        .filter(user=request.user, patient__in=queryset, archived_at__isnull=True)
        .update(archived_at=timezone.now())
    )
    modeladmin.message_user(request, f"Removed {updated} patient(s) from your watchlist.", level=messages.SUCCESS)

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
        "attending",
        "on_my_watchlist",   # column already present
        "watch_toggle_link", # <-- NEW: per-row toggle link
        "admit_display",
        "status",
        "discharged_at",
        "archived_at",
    )
    list_filter = (
        "sex",
        "location",
        "attending",
        "admission_date",
        StatusListFilter,
        MyWatchlistFilter,
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

    inlines = [SignoutInline, AssignmentInline, TodoInline, OvernightEventInline]

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

    actions = ["add_to_my_watchlist_inline", "remove_from_my_watchlist_inline"]

    # ---------- Watchlist bulk actions ----------
    @admin.action(description="➕ Add selected to *my* watchlist")
    def add_to_my_watchlist_inline(self, request, queryset):
        from django.utils import timezone
        from .models import PatientWatch
        created, reactivated = 0, 0
        for patient in queryset:
            qs = PatientWatch.objects.filter(user=request.user, patient=patient)
            if qs.filter(archived_at__isnull=True).exists():
                continue
            if qs.filter(archived_at__isnull=False).exists():
                qs.update(archived_at=None)
                reactivated += 1
                continue
            PatientWatch.objects.create(user=request.user, patient=patient, note="")
            created += 1
        self.message_user(
            request,
            f"Added to your watchlist — created: {created}, reactivated: {reactivated}.",
            level=messages.SUCCESS,
        )

    @admin.action(description="➖ Remove selected from *my* watchlist")
    def remove_from_my_watchlist_inline(self, request, queryset):
        from django.utils import timezone
        from .models import PatientWatch
        qs = PatientWatch.objects.filter(user=request.user, patient__in=queryset, archived_at__isnull=True)
        count = qs.count()
        qs.update(archived_at=timezone.now())
        self.message_user(request, f"Removed {count} patient(s) from your watchlist.", level=messages.SUCCESS)

    # ---------- Quick toggle: Watch / Unwatch (per-row link) ----------
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path(
                "<int:patient_id>/toggle-watch/",
                self.admin_site.admin_view(self.toggle_watch),  # admin-protected
                name="patients_patient_toggle_watch",
            ),
        ]
        return custom + urls

    def toggle_watch(self, request, patient_id: int):
        """Toggle current user's watch (create/reactivate if missing, else archive)."""
        from django.utils import timezone
        from .models import Patient, PatientWatch

        patient = Patient.objects.filter(pk=patient_id).first()
        if not patient:
            return redirect(request.META.get("HTTP_REFERER", ".."))

        qs = PatientWatch.objects.filter(user=request.user, patient=patient)
        # If active exists -> archive it (unwatch)
        if qs.filter(archived_at__isnull=True).exists():
            qs.update(archived_at=timezone.now())
            self.message_user(request, f"Removed {patient.name} from your watchlist.", level=messages.SUCCESS)
        # Else if archived exists -> reactivate
        elif qs.filter(archived_at__isnull=False).exists():
            qs.update(archived_at=None)
            self.message_user(request, f"Re‑added {patient.name} to your watchlist.", level=messages.SUCCESS)
        # Else create new
        else:
            PatientWatch.objects.create(user=request.user, patient=patient, note="")
            self.message_user(request, f"Added {patient.name} to your watchlist.", level=messages.SUCCESS)

        # Go back to wherever you came from (keeps filters/sorting)
        return redirect(request.META.get("HTTP_REFERER", ".."))

    @admin.display(description="Watch toggle")
    def watch_toggle_link(self, obj):
        """Render a small Watch/Unwatch link per row."""
        is_on = bool(getattr(obj, "_on_my_watch", False))
        label = "Unwatch" if is_on else "Watch"
        return format_html('<a href="{}/toggle-watch/">{}</a>', obj.pk, label)


    readonly_fields = ("created_at", "updated_at", "discharged_at", "archived_at")

    # Efficiently annotate whether each row is on *my* watchlist
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _on_my_watch=Exists(
                PatientWatch.objects.filter(
                    user=request.user,
                    patient_id=OuterRef("pk"),
                    archived_at__isnull=True,
                )
            )
        )

    @admin.display(boolean=True, description="My watch?")
    def on_my_watchlist(self, obj):
        return bool(getattr(obj, "_on_my_watch", False))

    def changelist_view(self, request, extra_context=None):
        if "status__exact" not in request.GET:
            ref = request.META.get("HTTP_REFERER", "")
            # If landing fresh (no referrer) and no explicit filter, default to Active
            if not ref:
                q = request.GET.copy()
                q["status__exact"] = PatientStatus.ACTIVE
                request.GET = q
                request.META["QUERY_STRING"] = q.urlencode()
        return super().changelist_view(request, extra_context=extra_context)

    # ---------- Notification helpers ----------
    def _notify_assignment(self, patient: Patient, new_attending: User):
        """
        Create/queue a notification to the assigned hospitalist.
        Dedupes: removes any *pending* (future-visible) assignment notifications
        for this patient so only the final pre-7AM assignment fires.
        """
        now = timezone.now()
        visible_at = _next_visible_time(now)

        # 1) Cancel any pending overnight assignment notifications for this patient
        Notification.objects.filter(
            kind=Notification.Kind.ASSIGNMENT,
            patient=patient,
            visible_at__gt=now,  # future-visible (e.g., next 7 AM)
        ).delete()

        # 2) Push the latest assignment notification (respecting quiet hours)
        msg = (
            f"New patient assigned to you: {patient.mrn} — {patient.name}."
            f" Location: {patient.location or '—'}."
            f" Dx: {patient.diagnosis or '—'}."
        )
        Notification.push(
            recipient=new_attending,
            message=msg,
            level=Notification.Level.INFO,
            patient=patient,
            visible_at=visible_at,
            kind=Notification.Kind.ASSIGNMENT,
        )

    # ---------- Lifecycle Admin Actions ----------
    action_form = SetAttendingActionForm
    actions = [
        "bulk_set_or_clear_attending",
        "mark_active",
        "discharge_now",
        "archive_now",
        add_to_my_watchlist,
        remove_from_my_watchlist,
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

        # CLEAR: set to placeholder (no notification sent)
        if clear:
            with transaction.atomic():
                updated = 0
                for p in queryset.select_for_update():
                    if p.attending_id != tba_user.id:
                        p._changed_by_user = request.user  # ensure audit 'changed_by'
                        p.attending = tba_user
                        p.save(update_fields=["attending"])
                        updated += 1
            self.message_user(
                request,
                f"Set Attending to TO BE ASSIGNED on {updated} patient(s).",
                level=messages.SUCCESS,
            )
            return

        # ASSIGN: set to selected user and notify them
        if attending_id:
            try:
                user = User.objects.get(pk=attending_id)
            except User.DoesNotExist:
                self.message_user(request, "Selected Attending user not found.", level=messages.ERROR)
                return

            with transaction.atomic():
                updated = 0
                for p in queryset.select_for_update():
                    if p.attending_id != user.id:
                        p._changed_by_user = request.user  # ensure audit 'changed_by'
                        p.attending = user
                        p.save(update_fields=["attending"])
                        updated += 1
                        if user.username != "to_be_assigned":
                            self._notify_assignment(p, user)

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
        ro = list(super().get_readonly_fields(request, obj))
        ro += ["age_years", "los_days", "admit_display"]
        if obj and obj.status != PatientStatus.ACTIVE:
            ro += [f.attname for f in obj._meta.concrete_fields]
        return tuple(sorted(set(ro)))

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status != PatientStatus.ACTIVE:
            return False
        return super().has_delete_permission(request, obj=obj)

    def get_inline_instances(self, request, obj=None):
        if obj and obj.status != PatientStatus.ACTIVE:
            return []
        return super().get_inline_instances(request, obj)

    # ---------- Misc formatting ----------
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        try:
            tba_user = User.objects.get(username="to_be_assigned")
            initial.setdefault("attending", tba_user.pk)
        except User.DoesNotExist:
            pass
        return initial

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "attending" in form.base_fields:
            w = form.base_fields["attending"].widget
            for attr in ("can_add_related", "can_change_related", "can_delete_related"):
                if hasattr(w, attr):
                    setattr(w, attr, False)
        return form

    def save_model(self, request, obj, form, change):
        obj._changed_by_user = request.user  # ensure audit 'changed_by'

        prev_attending = None
        if change and obj.pk and "attending" in getattr(form, "changed_data", []):
            try:
                prev = Patient.objects.get(pk=obj.pk)
                prev_attending = prev.attending
            except Patient.DoesNotExist:
                prev_attending = None

        super().save_model(request, obj, form, change)

        if change and "attending" in getattr(form, "changed_data", []):
            new_attending = obj.attending
            if new_attending and (not prev_attending or prev_attending.id != new_attending.id):
                if getattr(new_attending, "username", None) != "to_be_assigned":
                    self._notify_assignment(obj, new_attending)

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

# ========== Notifications Admin ==========

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("visible_at", "recipient", "level", "short_message", "is_read_flag", "created_at")
    list_display_links = ("visible_at", "short_message")
    list_filter = ("level", ("visible_at", admin.DateFieldListFilter), "recipient")
    search_fields = ("message", "recipient__username", "recipient__first_name", "recipient__last_name")
    actions = ["mark_as_read", "mark_as_unread"]
    date_hierarchy = "visible_at"
    ordering = ("-visible_at", "-created_at")

    def short_message(self, obj):
        msg = obj.message or ""
        return (msg[:80] + "…") if len(msg) > 80 else msg
    short_message.short_description = "Message"

    def is_read_flag(self, obj):
        return obj.read_at is not None
    is_read_flag.boolean = True
    is_read_flag.short_description = "Read?"

    @admin.action(description="Mark selected notifications as READ")
    def mark_as_read(self, request, queryset):
        updated = queryset.filter(read_at__isnull=True).update(read_at=timezone.now())
        self.message_user(request, f"Marked {updated} notification(s) as read.")

    @admin.action(description="Mark selected notifications as UNREAD")
    def mark_as_unread(self, request, queryset):
        updated = queryset.update(read_at=None)
        self.message_user(request, f"Marked {updated} notification(s) as unread.")

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

try:
    admin.site.unregister(Assignment)
except NotRegistered:
    pass
admin.site.register(Assignment, AssignmentAdmin)

# ========== Audit Logs Admin ==========
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "patient", "event", "old_attending", "new_attending", "changed_by")
    list_filter = ("event", ("created_at", admin.DateFieldListFilter), "changed_by")
    search_fields = (
        "patient__mrn",
        "patient__name",
        "changed_by__username",
        "changed_by__first_name",
        "changed_by__last_name",
        "old_attending__username",
        "new_attending__username",
    )
    ordering = ("-created_at",)
