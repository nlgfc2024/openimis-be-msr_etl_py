from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.adapters import ConfigurableAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.services import ConfigurableIndividualService, ConfigurableLocationService
from msr_etl.sources import ConfigurableSource


class ConfigurableServiceTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        MsrEtlConfig.sources = {
            "acme": {
                "base_url": "https://acme.example.org/records",
                "field_map": {"dob": "birth_date"},
            }
        }

    def tearDown(self):
        MsrEtlConfig.sources = self._original_sources

    def test_requires_source_type(self):
        with self.assertRaises(ValueError):
            ConfigurableIndividualService(user=MagicMock(), source_type=None, sink=MagicMock())

    def test_raises_when_source_type_not_configured(self):
        with self.assertRaises(ValueError):
            ConfigurableIndividualService(user=MagicMock(), source_type="does-not-exist", sink=MagicMock())

    def test_builds_configurable_source_and_adapter_from_config(self):
        service = ConfigurableIndividualService(
            user=MagicMock(), source_type="acme", sink=MagicMock(),
        )

        self.assertIsInstance(service.source, ConfigurableSource)
        self.assertEqual(service.source.config["base_url"], "https://acme.example.org/records")
        self.assertIsInstance(service.adapter, ConfigurableAdapter)
        self.assertEqual(service.adapter.field_map, {"dob": "birth_date"})

    def test_explicit_source_and_adapter_are_not_overridden(self):
        fake_source = MagicMock()
        fake_adapter = MagicMock()

        service = ConfigurableIndividualService(
            user=MagicMock(),
            source_type="acme",
            source=fake_source,
            adapter=fake_adapter,
            sink=MagicMock(),
        )

        self.assertIs(service.source, fake_source)
        self.assertIs(service.adapter, fake_adapter)

    @patch("msr_etl.services.configurable_service.IndividualImportSink")
    def test_individual_service_defaults_to_individual_sink(self, mock_sink_class):
        service = ConfigurableIndividualService(user=MagicMock(), source_type="acme")

        mock_sink_class.assert_called_once()
        self.assertIs(service.sink, mock_sink_class.return_value)

    @patch("msr_etl.services.configurable_service.LocationImportSink")
    def test_location_service_defaults_to_location_sink(self, mock_sink_class):
        service = ConfigurableLocationService(user=MagicMock(), source_type="acme")

        mock_sink_class.assert_called_once()
        self.assertIs(service.sink, mock_sink_class.return_value)
