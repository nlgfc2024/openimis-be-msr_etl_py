from django.test import SimpleTestCase

from msr_etl.adapters import ConfigurableAdapter
from msr_etl.adapters.base import DataAdapter


class ConfigurableAdapterInitTestCase(SimpleTestCase):

    def test_requires_non_empty_field_map(self):
        with self.assertRaises(DataAdapter.Error):
            ConfigurableAdapter(field_map={})

    def test_requires_field_map(self):
        with self.assertRaises(DataAdapter.Error):
            ConfigurableAdapter(field_map=None)


class ConfigurableAdapterTransformTestCase(SimpleTestCase):

    def test_raises_on_none_input(self):
        adapter = ConfigurableAdapter(field_map={"dob": "birth_date"})
        with self.assertRaises(DataAdapter.Error):
            adapter.transform(None)

    def test_flat_rename(self):
        adapter = ConfigurableAdapter(field_map={
            "first_name": "given_name",
            "dob": "birth_date",
        })

        result = adapter.transform([
            {"given_name": "Grace", "birth_date": "1990-01-01"},
        ])

        self.assertEqual(result, [{"first_name": "Grace", "dob": "1990-01-01"}])

    def test_nested_dotted_path(self):
        adapter = ConfigurableAdapter(field_map={
            "location_code": "village.code",
        })

        result = adapter.transform([
            {"village": {"code": "V001", "name": "Chitipa"}},
        ])

        self.assertEqual(result, [{"location_code": "V001"}])

    def test_missing_field_resolves_to_none(self):
        adapter = ConfigurableAdapter(field_map={"dob": "birth_date"})

        result = adapter.transform([{"other_field": "x"}])

        self.assertEqual(result, [{"dob": None}])

    def test_missing_intermediate_segment_resolves_to_none(self):
        adapter = ConfigurableAdapter(field_map={"location_code": "village.code"})

        result = adapter.transform([{"other_field": "x"}])

        self.assertEqual(result, [{"location_code": None}])

    def test_empty_records_list(self):
        adapter = ConfigurableAdapter(field_map={"dob": "birth_date"})

        self.assertEqual(adapter.transform([]), [])
