from django.test import SimpleTestCase

from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.source_registry import (
    UnknownSourceType,
    list_individual_source_types,
    list_location_source_types,
    resolve_individual_source,
    resolve_location_source,
)
from msr_etl.sources import UBRIndividualSource, UBRLocationSource


class ResolveIndividualSourceTestCase(SimpleTestCase):

    def test_defaults_to_ubr(self):
        source_cls, adapter_cls = resolve_individual_source()
        self.assertIs(source_cls, UBRIndividualSource)
        self.assertIs(adapter_cls, UBRIndividualAdapter)

    def test_explicit_ubr(self):
        source_cls, adapter_cls = resolve_individual_source("ubr")
        self.assertIs(source_cls, UBRIndividualSource)
        self.assertIs(adapter_cls, UBRIndividualAdapter)

    def test_unknown_source_type_raises(self):
        with self.assertRaises(UnknownSourceType):
            resolve_individual_source("does-not-exist")


class ResolveLocationSourceTestCase(SimpleTestCase):

    def test_defaults_to_ubr(self):
        source_cls, adapter_cls = resolve_location_source()
        self.assertIs(source_cls, UBRLocationSource)
        self.assertIs(adapter_cls, UBRLocationAdapter)

    def test_unknown_source_type_raises(self):
        with self.assertRaises(UnknownSourceType):
            resolve_location_source("does-not-exist")


class ListSourceTypesTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources

    def tearDown(self):
        MsrEtlConfig.sources = self._original_sources

    def test_includes_ubr_with_no_configured_sources(self):
        MsrEtlConfig.sources = {}
        self.assertEqual(list_individual_source_types(), ["ubr"])
        self.assertEqual(list_location_source_types(), ["ubr"])

    def test_both_kind_appears_in_both_lists(self):
        MsrEtlConfig.sources = {"acme": {"base_url": "https://example.org"}}
        self.assertIn("acme", list_individual_source_types())
        self.assertIn("acme", list_location_source_types())

    def test_individual_only_kind_excluded_from_location_list(self):
        MsrEtlConfig.sources = {"acme": {"base_url": "https://example.org", "kind": "individual"}}
        self.assertIn("acme", list_individual_source_types())
        self.assertNotIn("acme", list_location_source_types())

    def test_location_only_kind_excluded_from_individual_list(self):
        MsrEtlConfig.sources = {"acme": {"base_url": "https://example.org", "kind": "location"}}
        self.assertIn("acme", list_location_source_types())
        self.assertNotIn("acme", list_individual_source_types())
