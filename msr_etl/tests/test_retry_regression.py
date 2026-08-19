from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import AsyncJob
from core.services import ProgressReporter
from core.test_helpers import create_test_interactive_user
from msr_etl.jobs import _finish
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.scheduled_tasks import sweep_sync_units
from msr_etl.staging import sync_staged_units


def _make_job(user, status=AsyncJob.Status.RUNNING):
    return AsyncJob.objects.create(
        module="msr_etl", job_type="ubr_locations_import", task="x.y", user=user, status=status,
    )


def _make_unit(job_uuid):
    return MsrEtlSyncUnit.objects.create(
        job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.GVH, unit_code="101",
        stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.PENDING,
        raw_payload={"data_type": "G", "data": [{"geo_location_code": "1010101"}]},
    )


class FailRetrySucceedRegressionTestCase(TestCase):
    # Only the sink/adapter boundary is mocked - sync_staged_units, _finish,
    # and sweep_sync_units all run for real.

    @patch("msr_etl.staging.LocationImportSink")
    @patch("msr_etl.staging.UBRLocationAdapter")
    def test_finish_retries_inline_and_self_closes(self, mock_adapter_class, mock_sink_class):
        # A normal live run resolves a transient failure on its own, without
        # ever needing the sweeper.
        user = create_test_interactive_user(username="retry_regression_tester")
        job = _make_job(user)
        mock_adapter_class.return_value.transform.return_value = [{"code": "x", "type": "G"}]
        mock_sink_class.return_value.push.side_effect = [RuntimeError("transient UBR error"), None]
        unit = _make_unit(job.id)

        reporter = ProgressReporter(job)
        reporter.set_total(1)
        sync_staged_units(job.id, reporter=reporter, user=user)

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.FAILED)
        self.assertEqual(unit.attempts, 1)

        _finish(reporter)

        unit.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.SYNCED)
        self.assertEqual(unit.attempts, 1)
        self.assertEqual(job.status, AsyncJob.Status.SUCCESS)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(mock_sink_class.return_value.push.call_count, 2)

    @patch("msr_etl.staging.LocationImportSink")
    @patch("msr_etl.staging.UBRLocationAdapter")
    def test_sweeper_recovers_a_job_whose_worker_died_before_finish(self, mock_adapter_class, mock_sink_class):
        # Simulates a crash: the sync failed, but _finish never got to run
        # its retry loop. Only then is the sweeper the one to retry/close it.
        user = create_test_interactive_user(username="retry_regression_tester_2")
        job = _make_job(user)
        mock_adapter_class.return_value.transform.return_value = [{"code": "x", "type": "G"}]
        mock_sink_class.return_value.push.side_effect = [RuntimeError("transient UBR error"), None]
        unit = _make_unit(job.id)

        reporter = ProgressReporter(job)
        reporter.set_total(1)
        sync_staged_units(job.id, reporter=reporter, user=user)

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.FAILED)
        self.assertEqual(unit.attempts, 1)

        AsyncJob.objects.filter(id=job.id).update(updated_at=timezone.now() - timedelta(minutes=15))
        sweep_sync_units()

        unit.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.SYNCED)
        self.assertEqual(job.status, AsyncJob.Status.SUCCESS)
        self.assertIsNotNone(job.finished_at)
