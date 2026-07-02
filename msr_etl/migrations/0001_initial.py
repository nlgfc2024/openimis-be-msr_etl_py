# Generated manually for msr_etl persistent initial pull status tracking.
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="UBRLocationInitialPullStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.CharField(db_index=True, max_length=128, unique=True)),
                ("status", models.CharField(max_length=32)),
                ("message", models.TextField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "msr_etl_ubr_location_initial_pull_status",
            },
        ),
    ]
