from unittest.mock import patch

from django.test import SimpleTestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.schema import _source_type_options

SOURCES = {
    "pwp": {"connector": "openimis_gql", "display_name": "Rural Micro Enterprise Program (RMEP)",
            "source_name": "Public Works Program (PWP)", "filter_schema": {"individual": [{"name": "location"}]}},
    "sctp": {"connector": "msr_form_numbers", "display_name": "Ultra-Poor Graduation (UPG)"},
}


@patch.object(MsrEtlConfig, "sources", SOURCES)
class SourceTypeOptionsTestCase(SimpleTestCase):

    def test_label_names_the_programme_and_source_name_the_data_source(self):
        (option,) = _source_type_options(["pwp"], "individual")

        self.assertEqual(option.label, "Rural Micro Enterprise Program (RMEP)")
        self.assertEqual(option.source_name, "Public Works Program (PWP)")
        self.assertEqual(option.filter_schema, [{"name": "location"}])

    def test_source_name_falls_back_to_display_name(self):
        (option,) = _source_type_options(["sctp"], "individual")

        self.assertEqual(option.source_name, "Ultra-Poor Graduation (UPG)")
