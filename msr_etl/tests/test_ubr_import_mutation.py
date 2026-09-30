from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.gql_mutations import (
    MsrEtlServiceMutation,
    ScheduleMsrUbrIndividualsImportMutation,
    ScheduleMsrUbrLocationsImportMutation,
)
from msr_etl.services import UBRIndividualService, UBRLocationService

INDIVIDUAL_FILTER_NAMES = [
    "lower_percentile_category", "upper_percentile_category", "wealth_quintiles", "classification",
    "gender", "minAge", "maxAge", "has_labour", "labour_constrained",
    "excluded_programme_codes", "household_head_gender",
]


class FilterSchemaTestMixin:

    def setUp(self):
        super().setUp()
        self._original_sources = MsrEtlConfig.sources
        MsrEtlConfig.sources = {
            "ubr": {
                "filter_schema": {
                    "individual": [{"name": "location", "type": "location", "required": True}]
                    + [{"name": name, "type": "text"} for name in INDIVIDUAL_FILTER_NAMES],
                    "location": [{"name": "location", "type": "location"}],
                },
            },
        }
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)


class ScheduleMsrUbrIndividualsImportMutationTestCase(FilterSchemaTestMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.user = MagicMock()
        self.user.id = 1
        self.user.has_perms.return_value = True
        self.supported_filters = {
            "district": "101",
            "ta": "10101",
            "gvh": "1010101",
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

    def test_ubr_individual_service_allows_district_only(self):
        service = UBRIndividualService(self.user, district="101", sink=MagicMock())
        self.assertEqual(service.source.district, "101")
        self.assertIsNone(service.source.ta)

    def test_ubr_individual_service_requires_district(self):
        with self.assertRaisesRegex(
            ValueError,
            "district is required",
        ):
            UBRIndividualService(self.user)

    def test_ubr_individual_service_requires_gvh_with_village(self):
        with self.assertRaisesRegex(
            ValueError,
            "gvh is required when village is provided",
        ):
            UBRIndividualService(
                self.user,
                district="101",
                ta="10101",
                village="101010101",
            )

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    @patch("msr_etl.gql_mutations.MsrEtlServiceMutation._get_supported_service_kwargs")
    @patch("msr_etl.gql_mutations.UBRIndividualService")
    def test_mutation_schedules_job_and_returns_immediately(
        self, mock_service_class, mock_supported_kwargs, mock_dispatch,
    ):
        mock_supported_kwargs.return_value = self.supported_filters
        mock_dispatch.return_value = "async-job-uuid"

        result = ScheduleMsrUbrIndividualsImportMutation.async_mutate(
            self.user,
            client_mutation_id="mutation-id",
            client_mutation_label="MSR auto import",
            source_type="ubr",
            filters=self.supported_filters,
        )

        self.assertIsNone(result)
        mock_supported_kwargs.assert_called_once()
        # constructed for validation only - never executed synchronously
        mock_service_class.assert_called_once_with(self.user, **self.supported_filters)
        mock_service_class.return_value.execute.assert_not_called()
        mock_dispatch.assert_called_once_with(
            "msr_etl.jobs.run_ubr_individuals_import",
            module="msr_etl",
            job_type="ubr_individuals_import",
            user=self.user,
            params=self.supported_filters,
            client_mutation_id="mutation-id",
        )

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_mutation_returns_error_when_validation_fails(self, mock_dispatch):
        result = ScheduleMsrUbrIndividualsImportMutation.async_mutate(
            self.user,
            filters={"district": "101", "ta": "10101", "village": "101010101"},
        )

        self.assertEqual(len(result), 1)
        self.assertIn("gvh is required", result[0]["detail"])
        mock_dispatch.assert_not_called()

    @patch("msr_etl.gql_mutations.UBRIndividualService")
    def test_mutation_requires_execute_permission(self, mock_service_class):
        self.user.has_perms.return_value = False

        result = ScheduleMsrUbrIndividualsImportMutation.async_mutate(
            self.user,
            filters={"district": "101", "ta": "10101"},
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["message"],
            "Failed to process ScheduleMsrUbrIndividualsImportMutation mutation",
        )
        mock_service_class.assert_not_called()


class ScheduleMsrUbrLocationsImportMutationTestCase(FilterSchemaTestMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.user = MagicMock()
        self.user.id = 1
        self.user.has_perms.return_value = True

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_mutation_schedules_job_and_returns_immediately(self, mock_dispatch):
        mock_dispatch.return_value = "async-job-uuid"

        result = ScheduleMsrUbrLocationsImportMutation.async_mutate(
            self.user,
            client_mutation_id="mutation-id",
        )

        self.assertIsNone(result)
        mock_dispatch.assert_called_once_with(
            "msr_etl.jobs.run_ubr_locations_import",
            module="msr_etl",
            job_type="ubr_locations_import",
            user=self.user,
            params={},
            client_mutation_id="mutation-id",
        )

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_mutation_schedules_scoped_job_when_filters_given(self, mock_dispatch):
        mock_dispatch.return_value = "async-job-uuid"

        result = ScheduleMsrUbrLocationsImportMutation.async_mutate(
            self.user,
            client_mutation_id="mutation-id",
            filters={"district": "101", "ta": "10101", "gvh": "1010101"},
        )

        self.assertIsNone(result)
        mock_dispatch.assert_called_once_with(
            "msr_etl.jobs.run_ubr_locations_import",
            module="msr_etl",
            job_type="ubr_locations_import",
            user=self.user,
            params={"district": "101", "ta": "10101", "gvh": "1010101"},
            client_mutation_id="mutation-id",
        )

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_mutation_returns_error_when_validation_fails(self, mock_dispatch):
        result = ScheduleMsrUbrLocationsImportMutation.async_mutate(
            self.user,
            filters={"ta": "10101"},
        )

        self.assertEqual(len(result), 1)
        self.assertIn("district is required", result[0]["detail"])
        mock_dispatch.assert_not_called()

    def test_mutation_requires_execute_permission(self):
        self.user.has_perms.return_value = False

        result = ScheduleMsrUbrLocationsImportMutation.async_mutate(self.user)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["message"],
            "Failed to process ScheduleMsrUbrLocationsImportMutation mutation",
        )


class ScheduleFiltersAllowlistTestCase(FilterSchemaTestMixin, SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.user = MagicMock()
        self.user.id = 1
        self.user.has_perms.return_value = True

    def _schedule_individuals(self, filters, **extra):
        return ScheduleMsrUbrIndividualsImportMutation.async_mutate(self.user, filters=filters, **extra)

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_rejects_service_internals_passed_as_filters(self, mock_dispatch):
        for key in ("sink", "source", "adapter", "user"):
            with self.subTest(key=key):
                result = self._schedule_individuals({"district": "101", key: {"x": 1}})

                self.assertEqual(len(result), 1)
                self.assertIn(f"Unsupported filters for source_type 'ubr': {key}", result[0]["detail"])
        mock_dispatch.assert_not_called()

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_rejects_filters_not_in_the_schema(self, mock_dispatch):
        result = self._schedule_individuals({"district": "101", "unknown": 1, "another": 2})

        self.assertIn("another, unknown", result[0]["detail"])
        mock_dispatch.assert_not_called()

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_reserved_keys_stay_rejected_even_if_listed_in_schema(self, mock_dispatch):
        MsrEtlConfig.sources["ubr"]["filter_schema"]["individual"].append({"name": "sink", "type": "text"})

        result = self._schedule_individuals({"district": "101", "sink": "x"})

        self.assertIn("sink", result[0]["detail"])
        mock_dispatch.assert_not_called()

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_rejects_non_object_filters(self, mock_dispatch):
        result = self._schedule_individuals(["district"])

        self.assertIn("filters must be an object", result[0]["detail"])
        mock_dispatch.assert_not_called()

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_rejects_everything_when_source_has_no_schema_for_the_kind(self, mock_dispatch):
        MsrEtlConfig.sources["ubr"]["filter_schema"].pop("individual")

        result = self._schedule_individuals({"district": "101"})

        self.assertIn("district", result[0]["detail"])
        mock_dispatch.assert_not_called()

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_location_field_allows_hierarchy_keys(self, mock_dispatch):
        result = ScheduleMsrUbrLocationsImportMutation.async_mutate(
            self.user, filters={"district": "101", "ta": "10101", "gvh": "1010101", "village": "101010101"},
        )

        self.assertIsNone(result)
        mock_dispatch.assert_called_once()

    @patch("msr_etl.gql_mutations.run_as_scheduled_job")
    def test_location_kind_uses_its_own_schema(self, mock_dispatch):
        result = ScheduleMsrUbrLocationsImportMutation.async_mutate(
            self.user, filters={"district": "101", "gender": "Female"},
        )

        self.assertIn("gender", result[0]["detail"])
        mock_dispatch.assert_not_called()


class UBRLocationServiceValidationTestCase(SimpleTestCase):

    def setUp(self):
        self.user = MagicMock()

    def test_requires_district_when_ta_provided(self):
        with self.assertRaisesRegex(
            ValueError,
            "district is required when ta is provided",
        ):
            UBRLocationService(self.user, ta="10101")

    def test_requires_ta_when_gvh_provided(self):
        with self.assertRaisesRegex(
            ValueError,
            "ta is required when gvh is provided",
        ):
            UBRLocationService(self.user, district="101", gvh="1010101")

    def test_requires_gvh_when_village_provided(self):
        with self.assertRaisesRegex(
            ValueError,
            "gvh is required when village is provided",
        ):
            UBRLocationService(
                self.user, district="101", ta="10101", village="101010101",
            )
