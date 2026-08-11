from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.jobs import run_ubr_individuals_import, run_ubr_locations_import


class RunUbrIndividualsImportTestCase(SimpleTestCase):

    def setUp(self):
        self.reporter = MagicMock()
        self.reporter.job.user = "the-user"

    @patch("msr_etl.jobs.UBRIndividualService")
    def test_success_advances_and_succeeds(self, mock_service_class):
        mock_service_class.return_value.execute.return_value = {
            "success": True,
            "data": {"batches_processed": 1},
        }

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        mock_service_class.assert_called_once_with(
            "the-user", district="101", ta="10101",
        )
        self.reporter.set_total.assert_called_once_with(1)
        self.reporter.advance.assert_called_once_with(1)
        self.reporter.succeed.assert_called_once_with(result={"batches_processed": 1})
        self.reporter.fail.assert_not_called()

    @patch("msr_etl.jobs.UBRIndividualService")
    def test_failure_reports_detail(self, mock_service_class):
        mock_service_class.return_value.execute.return_value = {
            "success": False,
            "message": "Failed to execute ETL pipeline",
            "detail": "UBR API unavailable",
        }

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        self.reporter.fail.assert_called_once_with("UBR API unavailable")
        self.reporter.advance.assert_not_called()
        self.reporter.succeed.assert_not_called()

    @patch("msr_etl.jobs.UBRIndividualService")
    def test_failure_falls_back_to_message(self, mock_service_class):
        mock_service_class.return_value.execute.return_value = {
            "success": False,
            "message": "Failed to execute ETL pipeline",
            "detail": None,
        }

        run_ubr_individuals_import(self.reporter, district="101", ta="10101")

        self.reporter.fail.assert_called_once_with("Failed to execute ETL pipeline")


class RunUbrLocationsImportTestCase(SimpleTestCase):

    def setUp(self):
        self.reporter = MagicMock()
        self.reporter.job.user = "the-user"

    @patch("msr_etl.jobs.UBRLocationService")
    def test_success_advances_and_succeeds(self, mock_service_class):
        mock_service_class.return_value.execute.return_value = {
            "success": True,
            "data": {"batches_processed": 4},
        }

        run_ubr_locations_import(self.reporter)

        mock_service_class.assert_called_once_with("the-user")
        self.reporter.set_total.assert_called_once_with(1)
        self.reporter.advance.assert_called_once_with(1)
        self.reporter.succeed.assert_called_once_with(result={"batches_processed": 4})

    @patch("msr_etl.jobs.UBRLocationService")
    def test_failure_reports_detail(self, mock_service_class):
        mock_service_class.return_value.execute.return_value = {
            "success": False,
            "message": "Failed to execute ETL pipeline",
            "detail": "UBR API unavailable",
        }

        run_ubr_locations_import(self.reporter)

        self.reporter.fail.assert_called_once_with("UBR API unavailable")
