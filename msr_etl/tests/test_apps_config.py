from django.test import SimpleTestCase

from msr_etl.apps import DEFAULT_CONFIG, MsrEtlConfig


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


class GetSourceConfigDefaultsTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)

    def test_falls_back_to_ubr_defaults_when_nothing_saved(self):
        MsrEtlConfig.sources = None
        config = MsrEtlConfig.get_source_config("ubr")
        self.assertEqual(config["connector"], "msr_api")
        self.assertIn("individual", config["filter_schema"])

    def test_saved_ubr_without_filter_schema_keeps_the_default_schema(self):
        MsrEtlConfig.sources = {"ubr": {"base_url": "https://ubr.example.org", "auth_type": "basic"}}
        config = MsrEtlConfig.get_source_config("ubr")
        self.assertEqual(config["base_url"], "https://ubr.example.org")
        self.assertEqual(config["auth_type"], "basic")
        self.assertEqual(config["filter_schema"], DEFAULT_CONFIG["sources"]["ubr"]["filter_schema"])

    def test_saved_filter_schema_overrides_the_default(self):
        schema = {"individual": [{"name": "location", "type": "location"}]}
        MsrEtlConfig.sources = {"ubr": {"filter_schema": schema}}
        self.assertEqual(MsrEtlConfig.get_source_config("ubr")["filter_schema"], schema)

    def test_sources_without_defaults_get_only_their_saved_keys(self):
        MsrEtlConfig.sources = {"sctp": {"connector": "msr_api"}}
        self.assertEqual(MsrEtlConfig.get_source_config("sctp"), {"connector": "msr_api"})
