import uuid

from django.conf import settings
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
        FORM_NUMBERS = "FORM_NUMBERS", "Form numbers"
        KOBO_PAGE = "KOBO_PAGE", "Kobo page"

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


class MsrEtlUpload(models.Model):
    """
    An uploaded import file: households by form number (e.g. UPG's SCTP export) or a location list.
    It is read once on upload, so the import job works from the stored rows and never re-reads the file.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=64)
    file_name = models.CharField(max_length=255)
    form_numbers = models.JSONField(default=list)
    # Location lists: the file's places as read (location_file.read_location_file); codes are resolved on import
    locations = models.JSONField(default=list, blank=True)
    # {"row": <file row>, "value": <cell>, "reason": <why>} for rows that were not usable
    rejected_rows = models.JSONField(default=list)
    uploaded_by = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "msr_etl_upload"


class MsrEtlLocationCodeChange(models.Model):
    """
    A temporary location code replaced by another code on the same location. Kobo answers and other
    records written before the change carry the old code, so lookups follow these (current_location_code).
    """
    location_type = models.CharField(max_length=1)
    old_code = models.CharField(max_length=50, db_index=True)
    new_code = models.CharField(max_length=50)
    location_id = models.IntegerField()
    batch_identifier = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "msr_etl_location_code_change"


class KoboSubmission(models.Model):
    """
    A submission pulled from a Kobo form, kept for good: Kobo runs for a limited time, and every
    submission must stay traceable to the record it created, updated or was held from.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        IMPORTED = "IMPORTED", "Imported"
        UPDATED = "UPDATED", "Updated an existing record"
        HELD = "HELD", "Held for review"
        NO_NATIONAL_ID = "NO_NATIONAL_ID", "No national ID"
        INVALID = "INVALID", "Invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=64)
    asset_uid = models.CharField(max_length=64)
    kobo_id = models.BigIntegerField()
    kobo_uuid = models.CharField(max_length=64, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    data = models.JSONField()
    national_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    reason = models.TextField(blank=True, default="")
    individual = models.ForeignKey("individual.Individual", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "msr_etl_kobo_submission"
        constraints = [models.UniqueConstraint(fields=["source_type", "asset_uid", "kobo_id"],
                                               name="msr_etl_kobo_submission_unique")]


class MsrEtlSourceRun(models.Model):
    """
    The import job a source is running, one row per source. Starting a job locks this row, so the schedule,
    a webhook and a user can't start two at once (two Kobo runs would create the same applicants twice).
    """
    source_type = models.CharField(max_length=64, primary_key=True)
    job_uuid = models.UUIDField(null=True, blank=True)
    # Set while a job is being started, outside the lock (the job must be committed before it runs)
    claimed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "msr_etl_source_run"


class KoboFormVersion(models.Model):
    """
    A deployed version of a Kobo form, as kobo_form.normalise_form() describes it: steps, questions and choice
    labels. Kept per version so each submission is shown with the form it was filled in on.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=64)
    asset_uid = models.CharField(max_length=64)
    # Kobo's version uid, which submissions carry as __version__; blank when read from an exported XLSForm
    version_uid = models.CharField(max_length=64, blank=True, default="")
    version = models.CharField(max_length=64, blank=True, default="")
    form = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "msr_etl_kobo_form_version"
        constraints = [models.UniqueConstraint(fields=["source_type", "asset_uid", "version_uid", "version"],
                                               name="msr_etl_kobo_form_version_unique")]


class ApplicantDocument(models.Model):
    """
    A file an applicant attached in Kobo, copied into the MIS file storage. Kobo runs for a limited time, so
    every attachment is kept, whatever the submission's outcome; nothing is ever deleted. The row is the
    download queue too: PENDING until stored, FAILED (retried up to kobo_document_max_attempts) on errors.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        STORED = "STORED", "Stored"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(KoboSubmission, on_delete=models.PROTECT, related_name="documents")
    individual = models.ForeignKey("individual.Individual", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    # The Kobo reference (with the submission's form and _id), not Kobo's URL
    kobo_attachment_uid = models.CharField(max_length=64)
    question = models.CharField(max_length=255, blank=True, default="")
    document_type = models.CharField(max_length=128, blank=True, default="")
    filename = models.CharField(max_length=255, blank=True, default="")
    mimetype = models.CharField(max_length=128, blank=True, default="")
    size = models.BigIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    storage_path = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")
    stored_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "msr_etl_applicant_document"
        constraints = [models.UniqueConstraint(fields=["submission", "kobo_attachment_uid"],
                                               name="msr_etl_applicant_document_unique")]


class ApplicantDocumentAccess(models.Model):
    """Every opening of an applicant document: national IDs and bank statements are sensitive."""
    id = models.BigAutoField(primary_key=True)
    document = models.ForeignKey(ApplicantDocument, on_delete=models.PROTECT, related_name="accesses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=255, blank=True, default="")
    # inline (shown in the browser), attachment (downloaded) or table (a spreadsheet shown in the page)
    disposition = models.CharField(max_length=16)
    accessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "msr_etl_applicant_document_access"
