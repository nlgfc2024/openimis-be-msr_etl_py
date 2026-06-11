import logging
from unittest.mock import patch, MagicMock

from django.test import TestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.sources import UBRIndividualSource, UBRLocationSource
from core.test_helpers import create_test_interactive_user
from location.models import Location
import requests

MOCKED_UBR_RESPONSE_DATA = [
    {
        "error_occurred": False,
        "targeting_data": [
            {"firstName": "Alice", "lastName": "Brown", "id": 1001},
            {"firstName": "Bob", "lastName": "Green", "id": 1002}
        ],
    },
    {
        "error_occurred": False,
        "targeting_data": [
            {"firstName": "Charlie", "lastName": "Davis", "id": 1003}
        ],
    },
]


class UBRIndividualSourceTestCase(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.headers = {"Authorization": "Bearer test_token"}
        cls.url = "https://malawiubr.org/api/v2/get_households_data"

    def setUp(self):
        # Mocked hierarchy: 1 district, 2 TAs, 2 villages per TA
        self.mocked_district_codes = ["101"]
        self.mocked_ta_codes = ["10101", "10102"]
        self.mocked_village_codes = {
            "10101": ["10101001", "10101002"],
            "10102": ["10102001", "10102002"],
        }
        self.mocked_ubr_response_data = [
            {
                "error_occurred": False,
                "targeting_data": [
                    {"firstName": "Alice", "lastName": "Brown", "id": 1001},
                ],
            },
            {
                "error_occurred": False,
                "targeting_data": [
                    {"firstName": "Bob", "lastName": "Green", "id": 1002},
                ],
            },
            {
                "error_occurred": False,
                "targeting_data": [
                    {"firstName": "Charlie", "lastName": "Davis", "id": 1003},
                ],
            },
            {
                "error_occurred": False,
                "targeting_data": [
                    {"firstName": "Daisy", "lastName": "Smith", "id": 1004},
                ],
            },
        ]

    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_ubr_source_pull(self, mock_location_filter, mock_post):
        """
        Test that UBRIndividualSource.pull iterates over districts, TAs, and villages,
        and makes one API call per village with correct params.
        """
        # Setup the filter mock to return different codes based on the filter arguments
        def filter_side_effect(*args, **kwargs):
            if kwargs.get("type") == "D":
                # Districts
                mock_qs = MagicMock()
                mock_qs.values_list.return_value = self.mocked_district_codes
                return mock_qs
            elif kwargs.get("type") == "W":
                # TAs for district
                mock_qs = MagicMock()
                mock_qs.values_list.return_value = self.mocked_ta_codes
                return mock_qs
            elif kwargs.get("type") == "V":
                # Villages for TA
                ta_code = kwargs.get("parent__code")
                mock_qs = MagicMock()
                mock_qs.values_list.return_value = self.mocked_village_codes[ta_code]
                return mock_qs
            else:
                return MagicMock()

        mock_location_filter.side_effect = filter_side_effect

        # Mock the API responses for each village (4 villages)
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[0])),
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[1])),
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[2])),
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[3])),
        ]

        user = create_test_interactive_user(username="test_admin")
        source = UBRIndividualSource(get_auth_provider('noauth'), pmt_percentile_range=range(1, 3))

        pulled_data = []
        identifiers = []

        for rows, identifier in source.pull():
            pulled_data += rows
            identifiers.append(identifier)

        # There should be 4 API calls (one per village)
        self.assertEqual(mock_post.call_count, 4)
        # Each call should have correct params
        for i, call in enumerate(mock_post.call_args_list):
            _, kwargs = call
            params = kwargs["params"]
            self.assertIn(params["district_code"], self.mocked_district_codes)
            self.assertIn(params["traditional_authority_code"], self.mocked_ta_codes)
            self.assertIn(params["village_code"], sum(self.mocked_village_codes.values(), []))
            self.assertEqual(params["lower_percentile_category"], '1')
            self.assertEqual(params["upper_percentile_category"], '2')
            self.assertEqual(params["wealth_quintile"], "1,2,3")  # Assuming enum values are 1,2,3

        # All individuals should be present
        self.assertEqual(len(pulled_data), 4)
        self.assertEqual(pulled_data[0]["firstName"], "Alice")
        self.assertEqual(pulled_data[1]["firstName"], "Bob")
        self.assertEqual(pulled_data[2]["firstName"], "Charlie")
        self.assertEqual(pulled_data[3]["firstName"], "Daisy")

        # Identifiers should be unique per village and match the code structure
        self.assertEqual(len(set(identifiers)), 4)
        for ta_code in self.mocked_ta_codes:
            for village_code in self.mocked_village_codes[ta_code]:
                self.assertTrue(any(f"batch_101_{ta_code}_{village_code}_" in ident for ident in identifiers))

        logging.info(f"Successfully pulled {len(pulled_data)} households for all villages")


class UBRLocationSourceTestCase(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.headers = {"Authorization": "Bearer test_token"}
        cls.url = "https://malawiubr.org/api/v2/get_geo_locations"
        cls.session = requests.Session()

        # Mocked districts response
        cls.mocked_districts_response = {
            "error_occurred": False,
            "total_records": 2,
            "geo_locations": [
                {"geo_location_code": "101", "geo_location_name": "Chitipa", "parent_geo_location_code": None, "geo_location_type_id": 1, "geo_location_type_name": "DISTRICT"},
                {"geo_location_code": "102", "geo_location_name": "Karonga", "parent_geo_location_code": None, "geo_location_type_id": 1, "geo_location_type_name": "DISTRICT"},
            ],
        }

        # Mocked TAs response corresponding to the districts
        cls.mocked_tas_response = [
            {
                "error_occurred": False,
                "total_records": 2,
                "geo_locations": [
                    {"geo_location_code": "10101", "geo_location_name": "Kameme", "parent_geo_location_code": "101", "geo_location_type_id": 2, "geo_location_type_name": "TRADITIONAL_AUTHORITY"},
                    {"geo_location_code": "10102", "geo_location_name": "Mwabulambiya", "parent_geo_location_code": "101", "geo_location_type_id": 2, "geo_location_type_name": "TRADITIONAL_AUTHORITY"},
                ],
            },
            {
                "error_occurred": False,
                "total_records": 2,
                "geo_locations": [
                    {"geo_location_code": "10201", "geo_location_name": "Karonga TA1", "parent_geo_location_code": "102", "geo_location_type_id": 2, "geo_location_type_name": "TRADITIONAL_AUTHORITY"},
                    {"geo_location_code": "10202", "geo_location_name": "Karonga TA2", "parent_geo_location_code": "102", "geo_location_type_id": 2, "geo_location_type_name": "TRADITIONAL_AUTHORITY"},
                ],
            },
        ]

        # Mocked villages response corresponding to the districts
        cls.mocked_villages_response = [
            {
                "error_occurred": False,
                "total_records": 2,
                "geo_locations": [
                    {"geo_location_code": "101010101", "geo_location_name": "Mweniyanga II", "parent_geo_location_code": "10101", "geo_location_type_id": 11, "geo_location_type_name": "VILLAGE"},
                    {"geo_location_code": "101010102", "geo_location_name": "Mweniyanga IX", "parent_geo_location_code": "10101", "geo_location_type_id": 11, "geo_location_type_name": "VILLAGE"},
                ],
            },
            {
                "error_occurred": False,
                "total_records": 2,
                "geo_locations": [
                    {"geo_location_code": "102010101", "geo_location_name": "Karonga Village 1", "parent_geo_location_code": "10201", "geo_location_type_id": 11, "geo_location_type_name": "VILLAGE"},
                    {"geo_location_code": "102010102", "geo_location_name": "Karonga Village 2", "parent_geo_location_code": "10201", "geo_location_type_id": 11, "geo_location_type_name": "VILLAGE"},
                ],
            },
        ]

    @patch("requests.Session.post")
    def test_fetch_geo_locations_from_api(self, mock_post):
        # Test districts
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_districts_response))
        districts = UBRLocationSource.fetch_geo_locations_from_api(
            self.session, self.url, self.headers, {"geo_location_type_id": 1}, "districts"
        )
        self.assertEqual(len(districts), 2)
        self.assertEqual(districts[0]["geo_location_code"], "101")
        self.assertEqual(districts[1]["geo_location_code"], "102")

        # Test TAs
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[0]))
        tas = UBRLocationSource.fetch_geo_locations_from_api(
            self.session, self.url, self.headers, {"geo_location_type_id": 2, "district_code": "101"}, "TAs"
        )
        self.assertEqual(len(tas), 2)
        self.assertEqual(tas[0]["geo_location_code"], "10101")
        self.assertEqual(tas[1]["geo_location_code"], "10102")

        # Test Villages
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0]))
        villages = UBRLocationSource.fetch_geo_locations_from_api(
            self.session, self.url, self.headers, {"geo_location_type_id": 11, "district_code": "101"}, "Villages"
        )
        self.assertEqual(len(villages), 2)
        self.assertEqual(villages[0]["geo_location_code"], "101010101")
        self.assertEqual(villages[1]["geo_location_code"], "101010102")

    @patch("location.models.Location.objects.get_or_create")
    def test_ensure_regions_exist(self, mock_get_or_create):
        mock_get_or_create.return_value = (MagicMock(), True)

        UBRLocationSource.ensure_regions_exist()

        self.assertEqual(mock_get_or_create.call_count, 3)
        mock_get_or_create.assert_any_call(
            code="1",
            type="R",
            defaults={"name": "Northern"},
        )
        mock_get_or_create.assert_any_call(
            code="2",
            type="R",
            defaults={"name": "Central"},
        )
        mock_get_or_create.assert_any_call(
            code="3",
            type="R",
            defaults={"name": "Southern"},
        )

    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_pull(self, mock_location_filter, mock_post):
        # Mock the database query for districts
        mock_location_filter.return_value.values_list.return_value = ["101", "102"]

        # Mock the API responses for districts, TAs, and villages
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_districts_response)),  # Districts
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[0])),  # TAs for District 101
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0])),  # Villages for District 101
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[1])),  # TAs for District 102
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[1])),  # Villages for District 102
        ]

        # Call the pull method
        source = UBRLocationSource()
        results = list(source.pull())

        # Assertions
        self.assertEqual(len(results), 5)  # 1 for districts, 2 for TAs, 2 for villages
        self.assertEqual(results[0][0]["data_type"], "D")
        self.assertEqual(results[1][0]["data_type"], "W")
        self.assertEqual(results[2][0]["data_type"], "V")
        self.assertEqual(results[3][0]["data_type"], "W")
        self.assertEqual(results[4][0]["data_type"], "V")

        # Check the number of records in each result
        self.assertEqual(len(results[0][0]["data"]), 2)  # Districts
        self.assertEqual(len(results[1][0]["data"]), 2)  # TAs for District 101
        self.assertEqual(len(results[2][0]["data"]), 2)  # Villages for District 101
        self.assertEqual(len(results[3][0]["data"]), 2)  # TAs for District 102
        self.assertEqual(len(results[4][0]["data"]), 2)  # Villages for District 102

