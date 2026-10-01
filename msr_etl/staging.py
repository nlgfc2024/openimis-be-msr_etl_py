import logging

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.sinks import IndividualImportSink, LocationImportSink
from msr_etl.source_registry import resolve_individual_source, resolve_location_source

logger = logging.getLogger(__name__)

_LOCATION_UNIT_SYNC_PRIORITY = Case(
    When(unit_type=MsrEtlSyncUnit.UnitType.DISTRICT, then=Value(0)),
    When(unit_type=MsrEtlSyncUnit.UnitType.TA, then=Value(1)),
    When(unit_type=MsrEtlSyncUnit.UnitType.GVH, then=Value(2)),
    When(unit_type=MsrEtlSyncUnit.UnitType.VILLAGE, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)

_RESOLVERS = {
    MsrEtlSyncUnit.Kind.INDIVIDUAL: resolve_individual_source,
    MsrEtlSyncUnit.Kind.LOCATION: resolve_location_source,
}

_SINKS = {
    MsrEtlSyncUnit.Kind.INDIVIDUAL: IndividualImportSink,
    MsrEtlSyncUnit.Kind.LOCATION: LocationImportSink,
}


def _seen_record_identities(job_uuid):
    seen = set()
    identity_lists = MsrEtlSyncUnit.objects.filter(
        job_uuid=job_uuid,
        stage_status=MsrEtlSyncUnit.Status.STAGED,
        record_identities__isnull=False,
    ).values_list("record_identities", flat=True)
    for identities in identity_lists:
        for identity in identities or []:
            seen.add(tuple(identity))
    return seen


def _dedupe_rows(job_uuid, source, rows):
    """Drop rows already staged by sibling units of the same job."""
    seen = _seen_record_identities(job_uuid)
    unique_rows = []
    new_identities = []
    for row in rows:
        identity = source.record_identity(row)
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
            new_identities.append(list(identity))
        unique_rows.append(row)
    return unique_rows, new_identities


def stage_unit(job_uuid, source, unit, kind):
    """Fetch one unit from a StagedDataSource and stage its payload. List
    payloads are deduped via source.record_identity()."""
    sync_unit = MsrEtlSyncUnit.objects.create(
        job_uuid=job_uuid,
        source_type=source.source_type,
        kind=kind,
        unit_type=unit["unit_type"],
        unit_code=unit["unit_code"],
    )
    try:
        payload = source.fetch_unit(unit)
        fields = {}
        if isinstance(payload, list):
            payload, fields["record_identities"] = _dedupe_rows(job_uuid, source, payload)

        MsrEtlSyncUnit.objects.filter(id=sync_unit.id).update(
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            raw_payload=payload,
            record_count=source.count_records(payload),
            updated_at=timezone.now(),
            **fields,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to stage %s unit %s: %s", unit["unit_type"], unit["unit_code"], exc)
        MsrEtlSyncUnit.objects.filter(id=sync_unit.id).update(
            stage_status=MsrEtlSyncUnit.Status.FAILED,
            error_detail=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        return False


def _sync_unit_payload(unit, user):
    if unit.raw_payload is None:
        return
    _, adapter_cls = _RESOLVERS[unit.kind](unit.source_type)
    transformed = adapter_cls(source_type=unit.source_type).transform(unit.raw_payload)
    if transformed:
        _SINKS[unit.kind](user).push(transformed, f"job_{unit.job_uuid}_unit_{unit.id}")


@transaction.atomic
def _sync_one_unit(unit_id, user):
    unit = (
        MsrEtlSyncUnit.objects
        .select_for_update(skip_locked=True)
        .filter(id=unit_id, sync_status=MsrEtlSyncUnit.Status.PENDING)
        .first()
    )
    if unit is None:
        return None

    try:
        _sync_unit_payload(unit, user)
        MsrEtlSyncUnit.objects.filter(id=unit.id).update(
            sync_status=MsrEtlSyncUnit.Status.SYNCED,
            updated_at=timezone.now(),
        )
        return True
    except Exception as exc:
        logger.warning("Failed to sync unit %s: %s", unit.unit_code, exc)
        MsrEtlSyncUnit.objects.filter(id=unit.id).update(
            sync_status=MsrEtlSyncUnit.Status.FAILED,
            error_detail=str(exc)[:2000],
            attempts=unit.attempts + 1,
            updated_at=timezone.now(),
        )
        return False


def sync_staged_units(job_uuid, reporter=None, user=None):
    """Claims staged-but-unsynced rows in per-unit transactions, ordered
    DISTRICT -> TA -> GVH -> VILLAGE per district, and syncs each via
    Adapter -> Sink."""
    if user is None:
        from core.models import AsyncJob

        job = AsyncJob.objects.filter(id=job_uuid).first()
        user = job.user if job else None

    pending_ids = list(
        MsrEtlSyncUnit.objects
        .filter(
            job_uuid=job_uuid,
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.PENDING,
        )
        .annotate(_priority=_LOCATION_UNIT_SYNC_PRIORITY)
        .order_by("unit_code", "_priority")
        .values_list("id", flat=True)
    )

    for unit_id in pending_ids:
        result = _sync_one_unit(unit_id, user)
        if result is None or reporter is None:
            continue
        reporter.advance(**({"synced": 1} if result else {"errors": 1}))


def job_has_failed_units(job_uuid):
    return MsrEtlSyncUnit.objects.filter(job_uuid=job_uuid).filter(
        Q(stage_status=MsrEtlSyncUnit.Status.FAILED) | Q(sync_status=MsrEtlSyncUnit.Status.FAILED)
    ).exists()


def has_retryable_failed_units(job_uuid):
    """True if this job has sync-failed units still under sync_unit_max_attempts
    - i.e. worth another sweeper pass before the job is allowed to
    close."""
    max_attempts = int(MsrEtlConfig.sync_unit_max_attempts)
    return MsrEtlSyncUnit.objects.filter(
        job_uuid=job_uuid,
        stage_status=MsrEtlSyncUnit.Status.STAGED,
        sync_status=MsrEtlSyncUnit.Status.FAILED,
        attempts__lt=max_attempts,
    ).exists()


def requeue_retryable_failed_units(job_uuid):
    # Shared by jobs.py's inline retry loop and the sweeper's crash recovery.
    max_attempts = int(MsrEtlConfig.sync_unit_max_attempts)
    MsrEtlSyncUnit.objects.filter(
        job_uuid=job_uuid,
        stage_status=MsrEtlSyncUnit.Status.STAGED,
        sync_status=MsrEtlSyncUnit.Status.FAILED,
        attempts__lt=max_attempts,
    ).update(sync_status=MsrEtlSyncUnit.Status.PENDING, updated_at=timezone.now())
