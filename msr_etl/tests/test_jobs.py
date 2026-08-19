from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.jobs import run_ubr_individuals_import, run_ubr_locations_import


class RunUbrIndividualsImportTestCase(SimpleTestCase):

    def setUp(self):
        self.reporter = MagicMock()
        self.reporter.job.id = "job-uuid"
        self.reporter.job.user = "the-user"

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_individual_unit")
    @patch("msr_etl.jobs.enumerate_individual_units")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_stages_all_units_then_syncs_and_succeeds(
        self, mock_has_failed, mock_retryable, mock_enumerate, mock_stage, mock_sync,
    ):
        units = [{"unit_code": "101:10101:0-9"}, {"unit_code": "101:10101:10-19"}]
        mock_enumerate.return_value = ("source", units)
        mock_stage.return_value = True

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        mock_enumerate.assert_called_once_with("the-user", {"district": "101", "ta": "10101"})
        self.reporter.set_total.assert_called_once_with(4)
        self.assertEqual(mock_stage.call_count, 2)
        mock_sync.assert_called_once_with("job-uuid", reporter=self.reporter, user="the-user")
        self.reporter.succeed.assert_called_once()
        self.reporter.partial.assert_not_called()

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_individual_unit", return_value=False)
    @patch("msr_etl.jobs.enumerate_individual_units")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=True)
    def test_partial_when_units_failed(
        self, mock_has_failed, mock_retryable, mock_enumerate, mock_stage, mock_sync,
    ):
        mock_enumerate.return_value = ("source", [{"unit_code": "101:10101:0-9"}])

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        self.reporter.partial.assert_called_once()
        self.reporter.succeed.assert_not_called()

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_individual_unit", return_value=False)
    @patch("msr_etl.jobs.enumerate_individual_units")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=True)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=True)
    def test_stays_open_when_failed_units_are_still_retryable(
        self, mock_has_failed, mock_retryable, mock_enumerate, mock_stage, mock_sync,
    ):
        mock_enumerate.return_value = ("source", [{"unit_code": "101:10101:0-9"}])

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        self.reporter.partial.assert_not_called()
        self.reporter.succeed.assert_not_called()


class RunUbrLocationsImportTestCase(SimpleTestCase):

    def setUp(self):
        self.reporter = MagicMock()
        self.reporter.job.id = "job-uuid"
        self.reporter.job.user = "the-user"

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_location_unit", return_value=True)
    @patch("msr_etl.jobs.enumerate_location_units")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_stages_all_units_then_syncs_and_succeeds(
        self, mock_has_failed, mock_retryable, mock_enumerate, mock_stage, mock_sync,
    ):
        units = [
            {"district": "101", "unit_type": "DISTRICT"},
            {"district": "101", "unit_type": "TA"},
        ]
        mock_enumerate.return_value = ("source", units)

        run_ubr_locations_import(self.reporter)

        mock_enumerate.assert_called_once_with("the-user", {})
        self.reporter.set_total.assert_called_once_with(4)
        self.assertEqual(mock_stage.call_count, 2)
        mock_sync.assert_called_once_with("job-uuid", reporter=self.reporter, user="the-user")
        self.reporter.succeed.assert_called_once()

    @patch("msr_etl.jobs.sync_staged_units")
    @patch("msr_etl.jobs.stage_location_unit", return_value=True)
    @patch("msr_etl.jobs.enumerate_location_units")
    @patch("msr_etl.jobs.has_retryable_failed_units", return_value=False)
    @patch("msr_etl.jobs.job_has_failed_units", return_value=False)
    def test_stages_scoped_units_then_syncs_and_succeeds(
        self, mock_has_failed, mock_retryable, mock_enumerate, mock_stage, mock_sync,
    ):
        units = [{"district": "101", "unit_type": "GVH"}]
        mock_enumerate.return_value = ("source", units)

        run_ubr_locations_import(self.reporter, district="101", ta="10101", gvh="1010101")

        mock_enumerate.assert_called_once_with(
            "the-user", {"district": "101", "ta": "10101", "gvh": "1010101"},
        )
        self.reporter.set_total.assert_called_once_with(2)
        self.assertEqual(mock_stage.call_count, 1)
