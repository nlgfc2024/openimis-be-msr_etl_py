from unittest.mock import MagicMock

from django.test import SimpleTestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.schema import Query


class ResolveMsrEtlSourceTypesTestCase(SimpleTestCase):

    def setUp(self):
        self.info = MagicMock()
        self.info.context.user.has_perms.return_value = True
        self._original_sources = MsrEtlConfig.sources
        MsrEtlConfig.sources = {"acme": {"base_url": "https://example.org"}}

    def tearDown(self):
        MsrEtlConfig.sources = self._original_sources

    def test_denies_unauthorized_user(self):
        self.info.context.user.has_perms.return_value = False
        with self.assertRaises(PermissionError):
            Query.resolve_msr_etl_source_types(None, self.info)

    def test_includes_ubr_and_configured_sources(self):
        result = Query.resolve_msr_etl_source_types(None, self.info)
        individual_values = [option.value for option in result.individual_source_types]
        location_values = [option.value for option in result.location_source_types]
        self.assertIn("ubr", individual_values)
        self.assertIn("acme", individual_values)
        self.assertIn("ubr", location_values)
        self.assertIn("acme", location_values)

    def test_uses_configured_display_name_as_label(self):
        MsrEtlConfig.sources = {
            "acme": {"base_url": "https://example.org", "display_name": "ACME Corp"},
        }
        result = Query.resolve_msr_etl_source_types(None, self.info)
        by_value = {option.value: option.label for option in result.individual_source_types}
        self.assertEqual(by_value["acme"], "ACME Corp")

    def test_falls_back_to_no_label_when_display_name_unset(self):
        result = Query.resolve_msr_etl_source_types(None, self.info)
        by_value = {option.value: option.label for option in result.individual_source_types}
        self.assertIsNone(by_value["acme"])

    def test_includes_configured_filter_schema_scoped_by_kind(self):
        individual_schema = [{"name": "district", "label": "District", "type": "location", "required": True}]
        location_schema = [{"name": "district", "label": "District", "type": "location", "required": False}]
        MsrEtlConfig.sources = {
            "acme": {
                "base_url": "https://example.org",
                "filter_schema": {"individual": individual_schema, "location": location_schema},
            },
        }
        result = Query.resolve_msr_etl_source_types(None, self.info)
        by_individual_value = {option.value: option.filter_schema for option in result.individual_source_types}
        by_location_value = {option.value: option.filter_schema for option in result.location_source_types}
        self.assertEqual(by_individual_value["acme"], individual_schema)
        self.assertEqual(by_location_value["acme"], location_schema)

    def test_falls_back_to_empty_filter_schema_when_unset(self):
        result = Query.resolve_msr_etl_source_types(None, self.info)
        by_value = {option.value: option.filter_schema for option in result.individual_source_types}
        self.assertEqual(by_value["acme"], [])
