from django.db import migrations
from django.conf import settings

def set_null_attendings(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    Patient = apps.get_model("patients", "Patient")

    try:
        tba = User.objects.get(username="to_be_assigned")
    except User.DoesNotExist:
        # Safety: create if missing (should already exist from previous migration)
        tba = User.objects.create(username="to_be_assigned", first_name="TO BE", last_name="ASSIGNED", is_active=True)

    Patient.objects.filter(attending__isnull=True).update(attending=tba)

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0008_create_tba_attending_user"),
    ]

    operations = [
        migrations.RunPython(set_null_attendings, noop),
    ]
