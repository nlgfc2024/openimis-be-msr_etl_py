from django.db import models


class UBRWealthQuintiles(models.IntegerChoices):
    POOREST = 1, "Poorest"
    POORER = 2, "Poorer"
    POOR = 3, "Poor"
    BETTER = 4, "Better"
    RICH = 5, "Rich"


class UBRLocationInitialPullStatus(models.Model):
    request_id = models.CharField(max_length=128, unique=True, db_index=True)
    status = models.CharField(max_length=32)
    message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "msr_etl_ubr_location_initial_pull_status"
