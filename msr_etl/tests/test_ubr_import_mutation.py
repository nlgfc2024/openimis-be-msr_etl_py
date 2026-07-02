from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.gql_mutations import (
    ExecuteMsrUbrIndividualsImportMutation,
    MsrEtlServiceMutation,
)
from msr_etl.services import UBRIndividualService


class ExecuteMsrUbrIndividualsImportMutationTestCase(SimpleTestCase):

    def setUp(self):
        self.user = MagicMock()
        self.user.id = 1
        self.user.has_perms.return_value = True
        self.supported_filters = {
            "district": "101",
            "ta": "10101",
            "village": "101010101",
            "lower_percentile_category": 0,
            "upper_percentile_category": 20,
            "wealth_quintiles": [1, 2, 3],
            "classification": [2],
            "gender": "Female",
            "minAge": 18,
            "maxAge": 60,
            "has_labour": True,
            "labour_constrained": False,
            "excluded_programme_codes": ["SCTP"],
            "household_head_gender": 2,
        }

    def test_supported_service_kwargs_filters_to_ubr_individual_service_signature(self):
        data = {
            **self.supported_filters,
            "client_mutation_id": "mutation-id",
            "unsupported_value": "ignored",
            "empty_value": None,
        }

        result = MsrEtlServiceMutation._get_supported_service_kwargs(
            UBRIndividualService,
            data,
        )

        self.assertEqual(result, self.supported_filters)

    @patch("msr_etl.gql_mutations.MsrEtlServiceMutation._get_supported_service_kwargs")
    @patch("msr_etl.gql_mutations.UBRIndividualService")
    def test_mutation_executes_ubr_individual_service(self, mock_service_class, mock_supported_kwargs):
        mock_supported_kwargs.return_value = self.supported_filters
        service = mock_service_class.return_value
        service.execute.return_value = {
            "success": True,
            "data": {
                "batches_processed": 1,
                "source_records": 2,
                "transformed_records": 4,
                "batch_identifiers": ["batch_101_10101_20260702000000"],
            },
        }

        result = ExecuteMsrUbrIndividualsImportMutation.async_mutate(
            self.user,
            client_mutation_id="mutation-id",
            client_mutation_label="MSR auto import",
            unsupported_value="ignored",
            **self.supported_filters,
        )

        self.assertIsNone(result)
        mock_supported_kwargs.assert_called_once()
        mock_service_class.assert_called_once_with(self.user, **self.supported_filters)
        service.execute.assert_called_once_with()

    @patch("msr_etl.gql_mutations.UBRIndividualService")
    def test_mutation_returns_error_message_when_service_fails(self, mock_service_class):
        service = mock_service_class.return_value
        service.execute.return_value = {
            "success": False,
            "message": "Failed to execute ETL pipeline",
            "detail": "UBR API unavailable",
        }

        result = ExecuteMsrUbrIndividualsImportMutation.async_mutate(
            self.user,
            district="101",
        )

        self.assertEqual(result, [{
            "message": "Failed to execute ETL pipeline",
            "detail": "UBR API unavailable",
        }])

    @patch("msr_etl.gql_mutations.UBRIndividualService")
    def test_mutation_requires_execute_permission(self, mock_service_class):
        self.user.has_perms.return_value = False

        result = ExecuteMsrUbrIndividualsImportMutation.async_mutate(
            self.user,
            district="101",
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["message"],
            "Failed to process ExecuteMsrUbrIndividualsImportMutation mutation",
        )
        mock_service_class.assert_not_called()