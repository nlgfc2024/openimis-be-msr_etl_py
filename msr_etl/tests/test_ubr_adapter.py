import logging
from unittest import TestCase
from unittest.mock import patch, MagicMock

from msr_etl.adapters.ubr_adapter import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.apps import MsrEtlConfig

logger = logging.getLogger(__name__)


class UBRIndividualAdapterTestCase(TestCase):

    def setUp(self):
        self.adapter = UBRIndividualAdapter()

    def test_transform_valid_data(self):
        data = [
            {
                "form_number": " 12345 ",  # Ensure strip works
                "mobile_number": "0987654321",
                "village": {
                    "village_name": "Test Village",
                    "village_code": "2090551001",
                    "group_village_head": {
                        "group_village_head_code": "2090510",
                        "group_village_head_name": "Test GVH",
                        "traditional_authority": {
                            "traditional_authority_code": "20905",
                            "traditional_authority_name": "Test TA",
                            "district": {
                                "district_code": "209",
                                "district_name": "Test District",
                            }
                        }
                    }
                },
                "pmt_score": " 2.5 ",  # Ensure float conversion works
                "pmt_cut_off": {"wealth_quintile": "Poorest"},
                "household_members": [
                    {
                        "id": 1234,
                        "first_name": "Alice",
                        "last_name": "Doe",
                        "date_of_birth": "1980-03-03",
                        "relationship": {"parameter_name": "Head"},
                        "gender": {"parameter_name": "Female"},
                        "marital_status": {"parameter_name": "Divorced"},
                        "national_id": "123456",
                        "fit_for_work": 1,
                        "household_member_combined_responses": [
                            {
                                "general_parameter": {
                                    "parameter_id": 6,
                                    "parameter_name": "Not disabled",
                                },
                            },
                            {
                                "general_parameter": {
                                    "parameter_id": 7,
                                    "parameter_name": "None",
                                },
                            },
                        ],
                    },
                    {
                        "id": 1235,
                        "first_name": "Bob",
                        "last_name": "Doe",
                        "date_of_birth": "1979-04-04",
                        "relationship": {"parameter_name": "Spouse"},
                        "gender": {"parameter_name": "Male"},
                        "national_id": "234567",
                        "fit_for_work": 1,
                    },
                    {
                        "id": 1236,
                        "first_name": "John",
                        "last_name": "Doe",
                        "date_of_birth": "1990-01-01",
                        "relationship": {"parameter_name": "Own child"},
                        "gender": {"parameter_name": "Female"},
                        "national_id": "3456578",
                        "fit_for_work": 1,
                    },
                    {
                        "id": 1237,
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "date_of_birth": "1995-02-02",
                        "relationship": {"parameter_name": "Parent"},
                        "gender": {"parameter_name": "Female"},
                        "national_id": "",
                        "fit_for_work": 0,
                    },
                ],
            }
        ]

        transformed_data = self.adapter.transform(data)

        self.assertEqual(len(transformed_data), 4)
        head = transformed_data[0]
        self.assertEqual(head["first_name"], "Alice")
        self.assertEqual(head["last_name"], "Doe")
        self.assertEqual(head["dob"], "1980-03-03")
        self.assertEqual(head["individual_role"], "HEAD")
        self.assertEqual(head["group_code"], "12345")
        self.assertEqual(head["location_name"], "Test Village")
        self.assertEqual(head["location_code"], "2090551001")
        self.assertEqual(head["ubr_id"], 1234)
        self.assertEqual(head["national_id"], "123456")
        self.assertEqual(head["fit_for_work"], 1)
        self.assertEqual(head["gender"], 'Female')
        self.assertEqual(head["marital_status"], "Divorced")
        self.assertEqual(head["disability"], "Not disabled")
        self.assertEqual(head["household_mobile_number"], '0987654321')
        self.assertEqual(head["household_pmt_score"], 2.5)
        self.assertEqual(head["household_wealth_quintile"], "Poorest")
        self.assertEqual(head["group_village_head_name"], "Test GVH")
        self.assertEqual(head["group_village_head_code"], "2090510")
        self.assertEqual(head["traditional_authority_name"], "Test TA")
        self.assertEqual(head["traditional_authority_code"], "20905")

        # Members without marital_status/combined-responses in the source payload
        # should resolve to None rather than raising.
        self.assertIsNone(transformed_data[1]["marital_status"])
        self.assertIsNone(transformed_data[1]["disability"])

        self.assertEqual(transformed_data[1]["individual_role"], "SPOUSE")
        self.assertEqual(transformed_data[2]["individual_role"], "DAUGHTER")
        self.assertEqual(transformed_data[3]["individual_role"], "MOTHER")
        self.assertEqual(transformed_data[3]["national_id"], "")
        self.assertEqual(transformed_data[3]["fit_for_work"], 0)

    def test_transform_skips_invalid_household(self):
        data = [{"form_number": "  ", "household_members": []}]
        transformed_data = self.adapter.transform(data)
        self.assertEqual(len(transformed_data), 0)

    def test_transform_handles_none_data(self):
        with self.assertRaises(UBRIndividualAdapter.Error) as cm:
            self.adapter.transform(None)

        self.assertEqual(str(cm.exception), "Invalid input, expect input not to be None")

    def test_parse_marital_status_returns_parameter_name(self):
        member = {"marital_status": {"parameter_name": "Never married"}}
        self.assertEqual(self.adapter.parse_marital_status(member), "Never married")

    def test_parse_marital_status_missing_returns_none(self):
        self.assertIsNone(self.adapter.parse_marital_status({}))

    def test_parse_disability_matches_configured_parameter_id(self):
        member = {
            "household_member_combined_responses": [
                {"general_parameter": {"parameter_id": 7, "parameter_name": "None"}},
                {"general_parameter": {"parameter_id": 6, "parameter_name": "Mentally disabled"}},
            ],
        }
        self.assertEqual(self.adapter.parse_disability(member), "Mentally disabled")

    def test_parse_disability_missing_responses_returns_none(self):
        self.assertIsNone(self.adapter.parse_disability({}))
        self.assertIsNone(self.adapter.parse_disability({"household_member_combined_responses": []}))

    def test_parse_disability_uses_configured_parameter_id(self):
        member = {
            "household_member_combined_responses": [
                {"general_parameter": {"parameter_id": 6, "parameter_name": "Not disabled"}},
                {"general_parameter": {"parameter_id": 99, "parameter_name": "Physically disabled"}},
            ],
        }
        with patch.object(MsrEtlConfig, "ubr_disability_parameter_id", 99):
            self.assertEqual(self.adapter.parse_disability(member), "Physically disabled")

    def test_parse_individual_role_valid_cases(self):
        member_son = {"relationship": {"parameter_name": "Own child"}, "gender": {"parameter_name": "Male"}}
        self.assertEqual(self.adapter.parse_individual_role(member_son), "SON")

        member_grandmother = {"relationship": {"parameter_name": "Grandparent"}, "gender": {"parameter_name": "Female"}}
        self.assertEqual(self.adapter.parse_individual_role(member_grandmother), "GRANDMOTHER")

    def test_parse_individual_role_unknown_gender(self):
        member = {"relationship": {"parameter_name": "Parent"}, "gender": {"parameter_name": "Unknown"}}
        role = self.adapter.parse_individual_role(member)
        self.assertEqual(role, "PARENT")

    def test_parse_individual_role_unknown_role(self):
        member = {"relationship": {"parameter_name": "Uncle"}, "gender": {"parameter_name": "Male"}}
        role = self.adapter.parse_individual_role(member)

        self.assertEqual(role, "UNCLE")


class UBRLocationAdapterTestCase(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adapter = UBRLocationAdapter()

    def setUp(self):
        self.mocked_district_data = {
            "data_type": "D",
            "data": [
                {"geo_location_code": "101", "geo_location_name": "Chitipa"},
                {"geo_location_code": "102", "geo_location_name": "Karonga"},
            ],
        }

        self.mocked_ta_data = {
            "data_type": "T",
            "data": [
                {"geo_location_code": "10101", "geo_location_name": "Kameme", "parent_geo_location_code": "101"},
                {"geo_location_code": "10201", "geo_location_name": "Karonga TA1", "parent_geo_location_code": "102"},
            ],
        }

        self.mocked_village_data = {
            "data_type": "V",
            "data": [
                {"geo_location_code": "101010101", "geo_location_name": "Mweniyanga II", "parent_geo_location_code": "1010101"},
                {"geo_location_code": "102010101", "geo_location_name": "Karonga Village 1", "parent_geo_location_code": "1020101"},
            ],
        }

    def test_transform_districts(self):
        transformed_data = self.adapter.transform(self.mocked_district_data)

        self.assertEqual(len(transformed_data), 2)
        self.assertEqual(transformed_data[0]["code"], "101")
        self.assertEqual(transformed_data[0]["name"], "Chitipa")
        self.assertEqual(transformed_data[0]["type"], "R")
        self.assertIsNone(transformed_data[0]["parent"])

    @patch("location.models.Location.objects.get")
    def test_transform_tas(self, mock_get):
        # Mock the database query for district-as-region parents
        mock_get.side_effect = [
            MagicMock(code="101", name="Chitipa", type="R"),
            MagicMock(code="102", name="Karonga", type="R"),
        ]

        transformed_data = self.adapter.transform(self.mocked_ta_data)

        self.assertEqual(len(transformed_data), 2)
        self.assertEqual(transformed_data[0]["code"], "10101")
        self.assertEqual(transformed_data[0]["name"], "Kameme")
        self.assertEqual(transformed_data[0]["type"], "D")
        self.assertEqual(transformed_data[0]["parent"].code, "101")

    @patch("location.models.Location.objects.get")
    def test_transform_villages(self, mock_get):
        # Mock the database query for GVHs mapped to wards
        mock_get.side_effect = [
            MagicMock(code="1010101", name="GVH A", type="W"),
            MagicMock(code="1020101", name="GVH C", type="W"),
        ]

        transformed_data = self.adapter.transform(self.mocked_village_data)

        self.assertEqual(len(transformed_data), 2)
        self.assertEqual(transformed_data[0]["code"], "101010101")
        self.assertEqual(transformed_data[0]["name"], "Mweniyanga II")
        self.assertEqual(transformed_data[0]["type"], "V")
        self.assertEqual(transformed_data[0]["parent"].code, "1010101")

    def test_transform_no_data(self):
        empty_data = {"data_type": "D", "data": []}
        transformed_data = self.adapter.transform(empty_data)
        self.assertEqual(len(transformed_data), 0)

    def test_transform_invalid_data_type(self):
        invalid_data = {"data_type": "X", "data": [{"geo_location_code": "999", "geo_location_name": "Invalid"}]}
        transformed_data = self.adapter.transform(invalid_data)
        self.assertEqual(len(transformed_data), 0)

    def test_transform_handles_none_data(self):
        with self.assertRaises(UBRLocationAdapter.Error) as cm:
            self.adapter.transform(None)

        self.assertEqual(str(cm.exception), "Invalid input, expect input not to be None")
