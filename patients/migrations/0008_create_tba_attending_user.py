from django.db import migrations
from django.conf import settings


def create_tba_user(apps, schema_editor):
    """
    Ensure a placeholder Attending user exists for night/evening admits
    when the real attending is not yet known.
    """
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)

    username = "to_be_assigned"
    first_name = "TO BE"
    last_name = "ASSIGNED"

    user, created = User.objects.get_or_create(
        username=username,
        defaults=dict(
            first_name=first_name,
            last_name=last_name,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        ),
    )

    # Keep display fields in sync if they changed later
    changed = False
    if getattr(user, "first_name", None) != first_name:
        user.first_name = first_name
        changed = True
    if getattr(user, "last_name", None) != last_name:
        user.last_name = last_name
        changed = True
    if changed:
        user.save(update_fields=["first_name", "last_name"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    # IMPORTANT: Point this at your latest patients migration.
    dependencies = [
        ("patients", "0007_alter_todo_options_remove_patient_attending_provider"),
    ]

    operations = [
        migrations.RunPython(create_tba_user, noop),
    ]
