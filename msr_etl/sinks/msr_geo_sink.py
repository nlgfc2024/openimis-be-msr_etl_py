from django.db import transaction
from django.core.exceptions import ValidationError

from location.msr_reference import MsrReferenceWriter
from msr_etl.apps import MsrEtlConfig
from msr_etl.sinks.base import DataSink


class MsrGeoSink(DataSink):
    def __init__(self, user, source_type):
        self.user = user
        self.writer = MsrReferenceWriter(MsrEtlConfig.get_source_config(source_type).get("source_instance"))

    @transaction.atomic
    def push(self, data, batch_identifier=None):
        for snapshot in data:
            from django.apps import apps
            if apps.is_installed("mapping"):
                from mapping.synchronization import apply_unit, SnapshotRejected
                from msr_etl.models import MsrEtlSyncUnit
                receipt = snapshot.get("_mapping") or {}
                unit = MsrEtlSyncUnit.objects.select_for_update().filter(
                    pk=receipt.get("unit_id"), job_uuid=receipt.get("job_uuid"),
                    stage_status=MsrEtlSyncUnit.Status.STAGED,
                ).first() if receipt.get("unit_id") and receipt.get("job_uuid") else None
                if unit is None or unit.raw_payload != snapshot:
                    raise SnapshotRejected("MAPPING_STAGE_RECEIPT_REQUIRED_RESTAGE")
                apply_unit(unit, self.user)
                continue
            if snapshot.get("source_instance") != self.writer.source_instance:
                raise ValidationError("Staged MSR instance differs from the configured source instance")
            self.writer.push(snapshot["locations"])
            self.writer.replace_memberships(snapshot["memberships"])
