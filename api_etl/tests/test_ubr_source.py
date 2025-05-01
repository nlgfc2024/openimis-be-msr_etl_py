import logging
from unittest.mock import patch, MagicMock

from django.test import TestCase

from api_etl.apps import ApiEtlConfig
from api_etl.auth_provider import get_auth_provider
from api_etl.sources import UBRIndividualSource, UBRLocationSource
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
        self.mocked_district_codes = ["101", "102"]
        self.mocked_ubr_response_data = [
            {
                "error_occurred": False,
                "targeting_data": [
                    {"firstName": "Alice", "lastName": "Brown", "id": 1001},
                    {"firstName": "Bob", "lastName": "Green", "id": 1002},
                ],
            },
            {
                "error_occurred": False,
                "targeting_data": [
                    {"firstName": "Charlie", "lastName": "Davis", "id": 1003},
                ],
            },
        ]

    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_ubr_source_pull(self, mock_location_filter, mock_post):
        # Mock the database query for districts
        mock_location_filter.return_value.values_list.return_value = self.mocked_district_codes

        # Mock the API responses for each district and wealth quintile
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[0])),  # District 101, Quintile 1
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[1])),  # District 101, Quintile 2
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[0])),  # District 101, Quintile 3
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[1])),  # District 102, Quintile 1
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[0])),  # District 102, Quintile 2
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_ubr_response_data[1])),  # District 102, Quintile 3
        ]

        user = create_test_interactive_user(username="test_admin")
        source = UBRIndividualSource(get_auth_provider('noauth'), pmt_percentile_range=range(1, 3))

        pulled_data = []
        identifiers = []

        for rows, identifier in source.pull():
            pulled_data += rows
            identifiers.append(identifier)

        # Assertions for pulled data
        self.assertEqual(len(pulled_data), 9)  # 3 records per quintile × 3 quintiles × 2 districts
        self.assertEqual(pulled_data[0]["firstName"], "Alice")
        self.assertEqual(pulled_data[1]["firstName"], "Bob")
        self.assertEqual(pulled_data[2]["firstName"], "Charlie")

        # Assertions for identifiers
        self.assertEqual(len(identifiers), 6)  # 3 quintiles per district × 2 districts
        self.assertTrue(identifiers[0].startswith("batch_101_1_1_2_"))
        self.assertTrue(identifiers[1].startswith("batch_101_2_1_2_"))
        self.assertTrue(identifiers[2].startswith("batch_101_3_1_2_"))
        self.assertTrue(identifiers[3].startswith("batch_102_1_1_2_"))
        self.assertTrue(identifiers[4].startswith("batch_102_2_1_2_"))
        self.assertTrue(identifiers[5].startswith("batch_102_3_1_2_"))

        logging.info(f"Successfully pulled {len(pulled_data)} households")


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
            "total_records": 32,
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

        # Mocked villages response corresponding to the TAs
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
    def test_fetch_districts_from_api(self, mock_post):
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_districts_response))
        ]

        districts = UBRLocationSource.fetch_districts_from_api(self.session, self.url, self.headers)

        self.assertEqual(len(districts), 2)
        self.assertEqual(districts[0]["geo_location_code"], "101")
        self.assertEqual(districts[0]["geo_location_name"], "Chitipa")
        self.assertEqual(districts[1]["geo_location_code"], "102")
        self.assertEqual(districts[1]["geo_location_name"], "Karonga")

    @patch("requests.Session.post")
    def test_fetch_tas_from_api(self, mock_post):
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[0]))
        ]

        district_code = "101"
        tas = UBRLocationSource.fetch_tas_from_api(self.session, self.url, self.headers, district_code)

        self.assertEqual(len(tas), 2)
        self.assertEqual(tas[0]["geo_location_code"], "10101")
        self.assertEqual(tas[0]["geo_location_name"], "Kameme")
        self.assertEqual(tas[1]["geo_location_code"], "10102")
        self.assertEqual(tas[1]["geo_location_name"], "Mwabulambiya")

    @patch("requests.Session.post")
    def test_fetch_villages_from_api(self, mock_post):
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0]))
        ]

        district_code = "10101"
        villages = UBRLocationSource.fetch_villages_from_api(self.session, self.url, self.headers, district_code)

        self.assertEqual(len(villages), 2)
        self.assertEqual(villages[0]["geo_location_code"], "101010101")
        self.assertEqual(villages[0]["geo_location_name"], "Mweniyanga II")
        self.assertEqual(villages[1]["geo_location_code"], "101010102")
        self.assertEqual(villages[1]["geo_location_name"], "Mweniyanga IX")

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
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[1])),  # TAs for District 102
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0])),  # Villages for District 101
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

