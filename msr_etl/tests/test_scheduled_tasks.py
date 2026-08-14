from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from core.models import AsyncJob
from core.test_helpers import create_test_interactive_user
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.scheduled_tasks import (
    cleanup_staged_payloads,
    schedule_tasks,
    sweep_sync_units,
)

# comfortably past the default sync_orphan_grace_minutes (10)
STALE_MINUTES_AGO = 15


class ScheduleTasksTestCase(TestCase):

    def test_registers_sweeper_and_cleanup(self):
        scheduler = MagicMock()
        schedule_tasks(scheduler)
        job_ids = [call.kwargs["id"] for call in scheduler.add_job.call_args_list]
        self.assertIn("msr_etl_sweep_sync_units", job_ids)
        self.assertIn("msr_etl_cleanup_staged_payloads", job_ids)


class SweepSyncUnitsTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = create_test_interactive_user(username="sweep_tester")

    def _create_job(self, idle_minutes_ago=None, **kwargs):
        defaults = dict(module="msr_etl", job_type="ubr_individuals_import", task="x.y", user=self.user)
        defaults.update(kwargs)
        job = AsyncJob.objects.create(**defaults)
        if idle_minutes_ago is not None:
            AsyncJob.objects.filter(id=job.id).update(
                updated_at=timezone.now() - timedelta(minutes=idle_minutes_ago)
            )
            job.refresh_from_db()
        return job

    def test_requeues_failed_units_below_max_attempts_for_an_idle_job(self):
        job = self._create_job(idle_minutes_ago=STALE_MINUTES_AGO)
        below_cap = MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.FAILED, attempts=1,
        )
        at_cap = MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:10-19",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.FAILED, attempts=3,
        )

        with patch("msr_etl.scheduled_tasks.sync_staged_units"):
            sweep_sync_units()

        below_cap.refresh_from_db()
        at_cap.refresh_from_db()
        self.assertEqual(below_cap.sync_status, MsrEtlSyncUnit.Status.PENDING)
        self.assertEqual(at_cap.sync_status, MsrEtlSyncUnit.Status.FAILED)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_does_not_requeue_failed_units_for_a_still_live_job(self, mock_sync):
        # updated_at fresh (default from creation) - as if the job's own
        # reporter just wrote to it, not a crash
        job = self._create_job()
        failed_unit = MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.FAILED, attempts=1,
        )

        sweep_sync_units()

        failed_unit.refresh_from_db()
        self.assertEqual(failed_unit.sync_status, MsrEtlSyncUnit.Status.FAILED)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_syncs_orphaned_units_for_an_idle_job_with_a_reconstructed_reporter(self, mock_sync):
        job = self._create_job(idle_minutes_ago=STALE_MINUTES_AGO, status=AsyncJob.Status.RUNNING, total=4, processed=1)
        MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.PENDING,
        )

        sweep_sync_units()

        mock_sync.assert_called_once()
        args, kwargs = mock_sync.call_args
        self.assertEqual(args[0], job.id)
        self.assertIsNotNone(kwargs["reporter"])
        self.assertEqual(kwargs["user"], self.user)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_does_not_sync_orphaned_units_for_a_still_live_job(self, mock_sync):
        # this is the exact bug found live: a job still in its staging phase
        # has staged-but-unsynced units that look identical to a crash's
        # leftovers unless the job's own idleness is checked first
        job = self._create_job(status=AsyncJob.Status.RUNNING, total=256, processed=90)
        MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.DISTRICT, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.PENDING,
        )

        sweep_sync_units()

        mock_sync.assert_not_called()

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_skips_reporter_when_job_total_never_set(self, mock_sync):
        job = self._create_job(idle_minutes_ago=STALE_MINUTES_AGO, status=AsyncJob.Status.RUNNING)
        MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.PENDING,
        )

        sweep_sync_units()

        mock_sync.assert_called_once_with(job.id, reporter=None, user=self.user)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_closes_job_as_success_once_processed_reaches_total(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.RUNNING, total=2, processed=2)
        MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.SYNCED,
        )

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.SUCCESS)
        self.assertIsNotNone(job.finished_at)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_closes_job_as_partial_when_a_unit_failed(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.RUNNING, total=2, processed=2)
        MsrEtlSyncUnit.objects.create(
            job_uuid=job.id, unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.FAILED,
        )

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.PARTIAL)
        self.assertTrue(job.error)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_leaves_job_open_when_processed_below_total(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.RUNNING, total=4, processed=2)

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.RUNNING)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_ignores_jobs_that_never_set_total(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.RUNNING)

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.RUNNING)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_fails_stale_jobs_beyond_job_stale_after_hours(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.RUNNING)
        AsyncJob.objects.filter(id=job.id).update(created_at=timezone.now() - timedelta(hours=13))

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.FAILED)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_leaves_fresh_jobs_alone(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.RUNNING)

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.RUNNING)

    @patch("msr_etl.scheduled_tasks.sync_staged_units")
    def test_does_not_touch_already_terminal_jobs(self, mock_sync):
        job = self._create_job(status=AsyncJob.Status.SUCCESS, total=1, processed=1)
        AsyncJob.objects.filter(id=job.id).update(created_at=timezone.now() - timedelta(hours=13))

        sweep_sync_units()

        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.SUCCESS)
        self.assertIsNone(job.error)


class CleanupStagedPayloadsTestCase(TestCase):

    def _unit(self, sync_status, updated_hours_ago, payload=None):
        unit = MsrEtlSyncUnit.objects.create(
            job_uuid="55555555-5555-5555-5555-555555555555",
            unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK,
            unit_code="101:10101:0-9",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=sync_status,
            raw_payload=payload if payload is not None else [{"id": 1}],
        )
        MsrEtlSyncUnit.objects.filter(id=unit.id).update(
            updated_at=timezone.now() - timedelta(hours=updated_hours_ago)
        )
        return unit

    def test_purges_synced_units_past_retention(self):
        unit = self._unit(MsrEtlSyncUnit.Status.SYNCED, updated_hours_ago=49)

        cleanup_staged_payloads()

        unit.refresh_from_db()
        self.assertIsNone(unit.raw_payload)
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.SYNCED)

    def test_keeps_synced_units_within_retention(self):
        unit = self._unit(MsrEtlSyncUnit.Status.SYNCED, updated_hours_ago=1)

        cleanup_staged_payloads()

        unit.refresh_from_db()
        self.assertIsNotNone(unit.raw_payload)

    def test_never_purges_failed_units(self):
        unit = self._unit(MsrEtlSyncUnit.Status.FAILED, updated_hours_ago=100)

        cleanup_staged_payloads()

        unit.refresh_from_db()
        self.assertIsNotNone(unit.raw_payload)
