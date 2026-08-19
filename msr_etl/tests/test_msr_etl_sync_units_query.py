from unittest.mock import MagicMock

from django.test import TestCase

from msr_etl.models import MsrEtlSyncUnit
from msr_etl.schema import Query


class ResolveMsrEtlSyncUnitsTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "88888888-8888-8888-8888-888888888888"
        self.info = MagicMock()
        self.info.context.user.has_perms.return_value = True
        MsrEtlSyncUnit.objects.create(
            job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.DISTRICT, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.SYNCED,
        )
        MsrEtlSyncUnit.objects.create(
            job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.FAILED,
        )
        # a different job's unit must never leak in
        MsrEtlSyncUnit.objects.create(
            job_uuid="99999999-9999-9999-9999-999999999999", unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="999",
        )

    def test_denies_unauthorized_user(self):
        self.info.context.user.has_perms.return_value = False
        with self.assertRaises(PermissionError):
            Query.resolve_msr_etl_sync_units(None, self.info, job_uuid=self.job_uuid)

    def test_scopes_to_job_uuid(self):
        result = Query.resolve_msr_etl_sync_units(None, self.info, job_uuid=self.job_uuid)
        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.count, 2)

    def test_filters_by_sync_status(self):
        result = Query.resolve_msr_etl_sync_units(
            None, self.info, job_uuid=self.job_uuid, sync_status=MsrEtlSyncUnit.Status.FAILED,
        )
        self.assertEqual(result.count, 1)
        self.assertEqual(result.units[0].unit_type, MsrEtlSyncUnit.UnitType.TA)

    def test_filters_by_unit_type(self):
        result = Query.resolve_msr_etl_sync_units(
            None, self.info, job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.DISTRICT,
        )
        self.assertEqual(result.count, 1)

    def test_limit_and_offset(self):
        result = Query.resolve_msr_etl_sync_units(None, self.info, job_uuid=self.job_uuid, limit=1, offset=1)
        self.assertEqual(result.count, 1)
        self.assertEqual(result.total_count, 2)
