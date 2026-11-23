from django.db import transaction
from msr_etl.sinks.base import DataSink

class MsrGeoSink(DataSink):
    def __init__(self, user, source_type):
        self.user = user

    @transaction.atomic
    def push(self, data, batch_identifier=None):
        from msr_etl.geo_synchronization import apply_unit, SnapshotRejected
        from msr_etl.models import MsrEtlSyncUnit
        for snapshot in data:
            receipt = snapshot.get('_msr_geo') or {}
            unit = MsrEtlSyncUnit.objects.select_for_update().filter(
                pk=receipt.get('unit_id'), job_uuid=receipt.get('job_uuid'),
                stage_status=MsrEtlSyncUnit.Status.STAGED).first() if receipt.get('unit_id') and receipt.get('job_uuid') else None
            if unit is None or unit.raw_payload != snapshot:
                raise SnapshotRejected('MSR_GEO_STAGE_RECEIPT_REQUIRED_RESTAGE')
            apply_unit(unit, self.user)
