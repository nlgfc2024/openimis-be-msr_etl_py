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
        self.assertIn("ubr", result.individual_source_types)
        self.assertIn("acme", result.individual_source_types)
        self.assertIn("ubr", result.location_source_types)
        self.assertIn("acme", result.location_source_types)
