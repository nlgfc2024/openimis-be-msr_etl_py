"""Reuse host jobs/staging; add ordering and configuration checks around one writer."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from msr_etl.geo_configuration import configuration_fingerprint
from msr_etl.geo_access import require_district_scope
from msr_etl.models import MsrGeoSyncState


class SnapshotRejected(ValidationError):
    permanent = True


def refresh_configuration():
    from core.models import ModuleConfiguration
    from msr_etl.apps import MsrEtlConfig, DEFAULT_CONFIG
    MsrEtlConfig._load_config(ModuleConfiguration.get_or_default("msr_etl", DEFAULT_CONFIG))


def prepare_receipt(source, unit):
    policy = getattr(source, "policy", "msr_etl")
    if policy not in ("msr_etl",):
        raise SnapshotRejected("MSR_GEO_INVALID_POLICY")
    return {"version": 2, "unit_id": unit.pk, "job_uuid": str(unit.job_uuid), "policy": policy,
            "source_type": source.source_type, "source_instance": source.source_instance,
            "district_id": source.district_id,
            "fingerprint": configuration_fingerprint(source.client.config, policy)}


def current_source(receipt):
    from msr_etl.apps import MsrEtlConfig
    config = MsrEtlConfig.get_source_config(receipt["source_type"])
    if config.get("enabled", True) is not True or config.get("source_instance") != receipt["source_instance"]:
        raise SnapshotRejected("MSR_GEO_SOURCE_IDENTITY_CHANGED")
    return config


def validate_snapshot(snapshot, receipt):
    if snapshot.get("source_instance") != receipt["source_instance"]:
        raise SnapshotRejected("MSR_GEO_SOURCE_IDENTITY_CHANGED")
    rows, memberships = snapshot.get("locations"), snapshot.get("memberships")
    if not isinstance(rows, list) or not rows or not isinstance(memberships, list):
        raise SnapshotRejected("MSR_GEO_INCOMPLETE_SNAPSHOT")
    if any(row.get("district_id") != receipt["district_id"] for row in rows):
        raise SnapshotRejected("MSR_GEO_SNAPSHOT_SCOPE_MISMATCH")
    identities = [(row.get("type"), row.get("id")) for row in rows]
    if len(set(identities)) != len(identities):
        raise SnapshotRejected("MSR_GEO_DUPLICATE_SOURCE_IDENTITY")
    micro_ids = {row["id"] for row in rows if row.get("type") == "catchments" and row.get("is_micro") is True}
    member_ids = [entry.get("catchment_id") for entry in memberships]
    if len(set(member_ids)) != len(member_ids) or set(member_ids) != micro_ids:
        raise SnapshotRejected("MSR_GEO_INCOMPLETE_MEMBERSHIPS")
    village_ids = {row["id"] for row in rows if row.get("type") == "villages"}
    assigned = set()
    for entry in memberships:
        ids = entry.get("village_ids")
        if not isinstance(ids, list) or len(set(ids)) != len(ids) or not set(ids) <= village_ids or assigned.intersection(ids):
            raise SnapshotRejected("MSR_GEO_MEMBERSHIP_CONFLICT")
        assigned.update(ids)


def validate_receipt(unit):
    snapshot = unit.raw_payload or {}
    receipt = snapshot.get("_msr_geo")
    if (not isinstance(receipt, dict) or receipt.get("version") != 2
            or receipt.get("unit_id") != unit.pk or receipt.get("job_uuid") != str(unit.job_uuid)
            or receipt.get("source_type") != unit.source_type or str(receipt.get("district_id")) != unit.unit_code
            or receipt.get("policy") not in ("msr_etl",)):
        raise SnapshotRejected("MSR_GEO_STAGE_RECEIPT_REQUIRED_RESTAGE")
    return receipt


@transaction.atomic
def apply_unit(unit, user):
    receipt = validate_receipt(unit)
    refresh_configuration()  # A worker may have outlived a configuration change.
    config = current_source(receipt)
    fingerprint = configuration_fingerprint(config, receipt["policy"])
    if not constant_time_compare(fingerprint, receipt.get("fingerprint", "")):
        raise SnapshotRejected("MSR_GEO_CONFIG_CHANGED_RESTAGE")
    require_district_scope(user, receipt["source_instance"], receipt["district_id"], receipt["policy"])
    validate_snapshot(unit.raw_payload, receipt)
    state, _ = MsrGeoSyncState.objects.get_or_create(
        source_instance=receipt["source_instance"], district_msr_id=receipt["district_id"])
    state = MsrGeoSyncState.objects.select_for_update().get(pk=state.pk)
    # Recheck after waiting for a concurrent source/district transaction.
    refresh_configuration()
    if not constant_time_compare(configuration_fingerprint(current_source(receipt), receipt["policy"]), fingerprint):
        raise SnapshotRejected("MSR_GEO_CONFIG_CHANGED_RESTAGE")
    require_district_scope(user, receipt["source_instance"], receipt["district_id"], receipt["policy"])
    if unit.pk < state.last_applied_unit_id:
        raise SnapshotRejected("MSR_GEO_STALE_SNAPSHOT_RESTAGE")
    rows = unit.raw_payload["locations"]
    if unit.pk == state.last_applied_unit_id:
        return len(rows)
    # Keep the proven location writer and its stable reference/reviewed-link IDs.
    from location.msr_reference import MsrReferenceWriter
    writer = MsrReferenceWriter(receipt["source_instance"])
    writer.push(rows)
    writer.replace_memberships(unit.raw_payload["memberships"])
    from msr_etl.geography import publish_snapshot
    publish_snapshot(receipt["source_instance"], rows, unit.raw_payload["memberships"], unit.raw_payload.get("geometries", []))
    state.last_applied_unit_id = unit.pk
    state.last_successful_sync = timezone.now()
    state.save(update_fields=["last_applied_unit_id", "last_successful_sync"])
    return len(rows)
