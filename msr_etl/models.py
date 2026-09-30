from django.db import models


class UBRWealthQuintiles(models.IntegerChoices):
    POOREST = 1, "Poorest"
    POORER = 2, "Poorer"
    POOR = 3, "Poor"
    BETTER = 4, "Better"
    RICH = 5, "Rich"


class MsrEtlSyncUnit(models.Model):
    """
    One staged fetch unit for a core.AsyncJob msr_etl import. job_uuid is the
    AsyncJob handle (no FK - core stays domain-free). Staging and syncing are
    separate steps so a crash or retry never re-pays the UBR fetch.
    """

    class UnitType(models.TextChoices):
        DISTRICT = "DISTRICT", "District"
        TA = "TA", "TA"
        GVH = "GVH", "GVH"
        VILLAGE = "VILLAGE", "Village"
        PERCENTILE_CHUNK = "PERCENTILE_CHUNK", "Percentile chunk"

    class Kind(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        LOCATION = "location", "Location"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        STAGED = "STAGED", "Staged"
        SYNCED = "SYNCED", "Synced"
        FAILED = "FAILED", "Failed"

    job_uuid = models.UUIDField(db_index=True)
    # Which source_type staged this unit and for which import, so sync can
    # resolve the matching adapter and sink.
    source_type = models.CharField(max_length=64, default="ubr")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.INDIVIDUAL)
    unit_type = models.CharField(max_length=32, choices=UnitType.choices)
    unit_code = models.CharField(max_length=64)
    stage_status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    sync_status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    raw_payload = models.JSONField(null=True, blank=True)
    record_count = models.IntegerField(null=True, blank=True)
    record_identities = models.JSONField(null=True, blank=True)
    error_detail = models.TextField(null=True, blank=True)
    attempts = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "msr_etl_sync_unit"
        indexes = [models.Index(fields=["job_uuid", "sync_status"])]
