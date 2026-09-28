from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.jobs import run_ubr_individuals_import, run_ubr_locations_import
from msr_etl.models import MsrEtlSyncUnit


def _service_with_units(mock_service_class, units):
    source = MagicMock()
    source.enumerate_units.return_value = units
    mock_service_class.return_value.source = source
    return source


class RunUbrIndividualsImportTestCase(SimpleTestCase):

    def setUp(self):
        self.reporter = MagicMock()
        self.reporter.job.id = "job-uuid"
        self.reporter.job.user = "the-user"
        self.units = [
            {"unit_type": "PERCENTILE_CHUNK", "unit_code": "101:10101:0-9"},
            {"unit_type": "PERCENTILE_CHUNK", "unit_code": "101:10101:10-19"},
        ]

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_unit", return_value=True)
    @patch("msr_etl.jobs.UBRIndividualService")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_stages_all_units_then_syncs_and_succeeds(
        self, mock_has_failed, mock_retryable, mock_service_class, mock_stage, mock_sync,
    ):
        source = _service_with_units(mock_service_class, self.units)

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        mock_service_class.assert_called_once_with("the-user", district="101", ta="10101")
        self.reporter.set_total.assert_called_once_with(4)
        mock_stage.assert_any_call("job-uuid", source, self.units[0], MsrEtlSyncUnit.Kind.INDIVIDUAL)
        self.assertEqual(mock_stage.call_count, 2)
        mock_sync.assert_called_once_with("job-uuid", reporter=self.reporter, user="the-user")
        self.reporter.succeed.assert_called_once()
        self.reporter.partial.assert_not_called()

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_unit", return_value=False)
    @patch("msr_etl.jobs.UBRIndividualService")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=True)
    def test_partial_when_units_failed(
        self, mock_has_failed, mock_retryable, mock_service_class, mock_stage, mock_sync,
    ):
        _service_with_units(mock_service_class, self.units[:1])

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        self.reporter.partial.assert_called_once()
        self.reporter.succeed.assert_not_called()

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_unit", return_value=False)
    @patch("msr_etl.jobs.UBRIndividualService")
    @patch("msr_etl.jobs.requeue_retryable_failed_units")
    @patch("msr_etl.jobs.has_retryable_failed_units", side_effect=[True, False])
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_retries_inline_before_closing(
        self, mock_has_failed, mock_retryable, mock_requeue, mock_service_class, mock_stage, mock_sync,
    ):
        _service_with_units(mock_service_class, self.units[:1])

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        mock_requeue.assert_called_once_with("job-uuid")
        self.assertEqual(mock_sync.call_count, 2)
        self.reporter.succeed.assert_called_once()


class RunUbrLocationsImportTestCase(SimpleTestCase):

    def setUp(self):
        self.reporter = MagicMock()
        self.reporter.job.id = "job-uuid"
        self.reporter.job.user = "the-user"

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_unit", return_value=True)
    @patch("msr_etl.jobs.UBRLocationService")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_stages_all_units_then_syncs_and_succeeds(
        self, mock_has_failed, mock_retryable, mock_service_class, mock_stage, mock_sync,
    ):
        units = [
            {"unit_type": "DISTRICT", "unit_code": "101"},
            {"unit_type": "TA", "unit_code": "101"},
        ]
        source = _service_with_units(mock_service_class, units)

        run_ubr_locations_import(self.reporter)

        mock_service_class.assert_called_once_with("the-user")
        self.reporter.set_total.assert_called_once_with(4)
        mock_stage.assert_any_call("job-uuid", source, units[0], MsrEtlSyncUnit.Kind.LOCATION)
        self.assertEqual(mock_stage.call_count, 2)
        mock_sync.assert_called_once_with("job-uuid", reporter=self.reporter, user="the-user")
        self.reporter.succeed.assert_called_once()

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_unit", return_value=True)
    @patch("msr_etl.jobs.UBRLocationService")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_stages_scoped_units_then_syncs_and_succeeds(
        self, mock_has_failed, mock_retryable, mock_service_class, mock_stage, mock_sync,
    ):
        _service_with_units(mock_service_class, [{"unit_type": "GVH", "unit_code": "101"}])

        run_ubr_locations_import(self.reporter, district="101", ta="10101", gvh="1010101")

        mock_service_class.assert_called_once_with("the-user", district="101", ta="10101", gvh="1010101")
        self.reporter.set_total.assert_called_once_with(2)
        self.assertEqual(mock_stage.call_count, 1)
