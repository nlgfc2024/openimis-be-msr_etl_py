from django.test import SimpleTestCase

from msr_etl.apps import MsrEtlConfig


class GetSourceConfigTestCase(SimpleTestCase):

    def test_returns_empty_dict_for_unconfigured_source(self):
        self.assertEqual(MsrEtlConfig.get_source_config("does-not-exist"), {})

    def test_returns_configured_source_entry(self):
        original = MsrEtlConfig.sources
        try:
            MsrEtlConfig.sources = {"other": {"base_url": "https://example.org"}}
            self.assertEqual(
                MsrEtlConfig.get_source_config("other"),
                {"base_url": "https://example.org"},
            )
        finally:
            MsrEtlConfig.sources = original

    def test_handles_unset_sources(self):
        original = MsrEtlConfig.sources
        try:
            MsrEtlConfig.sources = None
            self.assertEqual(MsrEtlConfig.get_source_config("other"), {})
        finally:
            MsrEtlConfig.sources = original
