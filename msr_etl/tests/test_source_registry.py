from django.test import SimpleTestCase

from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.source_registry import (
    UnknownSourceType,
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
