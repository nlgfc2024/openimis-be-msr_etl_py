import logging

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.services import UBRIndividualService, UBRLocationService
from msr_etl.sinks import IndividualImportSink, LocationImportSink
from msr_etl.sources.ubr_source import UBRIndividualSource

logger = logging.getLogger(__name__)

_LOCATION_UNIT_SYNC_PRIORITY = Case(
    When(unit_type=MsrEtlSyncUnit.UnitType.DISTRICT, then=Value(0)),
    When(unit_type=MsrEtlSyncUnit.UnitType.TA, then=Value(1)),
    When(unit_type=MsrEtlSyncUnit.UnitType.GVH, then=Value(2)),
    When(unit_type=MsrEtlSyncUnit.UnitType.VILLAGE, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)


def enumerate_individual_units(user, params):
    """District x TA x percentile chunk, from the local Location table and
    arithmetic only - no UBR call."""
    source = UBRIndividualService(user, **params).source
    units = []
    for district_code in source.get_district_codes():
        for ta_code in source.get_ta_codes(district_code):
            for chunk in source.get_percentile_chunks():
                lower, upper = chunk.start, chunk.stop - 1
                units.append({
                    "district": district_code,
                    "ta": ta_code,
                    "percentile_range": chunk,
                    "unit_code": f"{district_code}:{ta_code}:{lower}-{upper}",
                })
    return source, units


def _seen_household_identities(job_uuid):
    seen = set()
    identity_lists = MsrEtlSyncUnit.objects.filter(
        job_uuid=job_uuid,
        unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK,
        stage_status=MsrEtlSyncUnit.Status.STAGED,
    ).values_list("record_identities", flat=True)
    for identities in identity_lists:
        for identity in identities or []:
            seen.add(tuple(identity))
    return seen


def stage_individual_unit(job_uuid, source, unit):
    """Fetch one district+TA+percentile-chunk unit and stage it, deduping
    against households already staged by sibling units of the same job."""
    sync_unit = MsrEtlSyncUnit.objects.create(
        job_uuid=job_uuid,
        unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK,
        unit_code=unit["unit_code"],
    )
    try:
        rows = source.fetch_unit(unit["district"], unit["ta"], unit["percentile_range"])
        seen = _seen_household_identities(job_uuid)
        unique_rows = []
        new_identities = []
        for row in rows:
            identity = UBRIndividualSource.get_household_identity(row)
            if identity is not None:
                if identity in seen:
                    continue
                seen.add(identity)
                new_identities.append(list(identity))
            unique_rows.append(row)

        MsrEtlSyncUnit.objects.filter(id=sync_unit.id).update(
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            raw_payload=unique_rows,
            record_count=len(unique_rows),
            record_identities=new_identities,
            updated_at=timezone.now(),
        )
        return True
    except Exception as exc:
        logger.warning("Failed to stage unit %s: %s", unit["unit_code"], exc)
        MsrEtlSyncUnit.objects.filter(id=sync_unit.id).update(
            stage_status=MsrEtlSyncUnit.Status.FAILED,
            error_detail=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        return False


def enumerate_location_units(user):
    """District list comes from UBR itself (it is the data being imported);
    everything below it is district x level."""
    source = UBRLocationService(user).source
    district_rows = source.list_districts()
    units = []
    for row in district_rows:
        district_code = row.get("geo_location_code")
        if not district_code:
            logger.warning("Skipping district row with no geo_location_code")
            continue
        units.append({
            "district": district_code,
            "unit_type": MsrEtlSyncUnit.UnitType.DISTRICT,
            "payload": {"data_type": "D", "data": [row]},
        })
        for unit_type in (
            MsrEtlSyncUnit.UnitType.TA,
            MsrEtlSyncUnit.UnitType.GVH,
            MsrEtlSyncUnit.UnitType.VILLAGE,
        ):
            units.append({"district": district_code, "unit_type": unit_type})
    return source, units


def stage_location_unit(job_uuid, source, unit):
    """District rows reuse the payload already fetched during enumeration;
    TA/GVH/Village each make their own per-district UBR call."""
    sync_unit = MsrEtlSyncUnit.objects.create(
        job_uuid=job_uuid,
        unit_type=unit["unit_type"],
        unit_code=unit["district"],
    )
    try:
        if unit["unit_type"] == MsrEtlSyncUnit.UnitType.DISTRICT:
            payload = unit["payload"]
        else:
            payload = source.fetch_unit(unit["district"], unit["unit_type"])

        MsrEtlSyncUnit.objects.filter(id=sync_unit.id).update(
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            raw_payload=payload,
            record_count=len(payload.get("data") or []),
            updated_at=timezone.now(),
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to stage %s unit for district %s: %s",
            unit["unit_type"], unit["district"], exc,
        )
        MsrEtlSyncUnit.objects.filter(id=sync_unit.id).update(
            stage_status=MsrEtlSyncUnit.Status.FAILED,
            error_detail=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        return False


def _sync_unit_payload(unit, user):
    if unit.unit_type == MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK:
        transformed = UBRIndividualAdapter().transform(unit.raw_payload or [])
        sink = IndividualImportSink(user)
    else:
        transformed = UBRLocationAdapter().transform(unit.raw_payload or {})
        sink = LocationImportSink(user)

    if transformed:
        sink.push(transformed, f"job_{unit.job_uuid}_unit_{unit.id}")


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
