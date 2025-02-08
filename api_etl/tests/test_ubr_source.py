import logging
from unittest.mock import patch, MagicMock

from django.test import TestCase

from api_etl.apps import ApiEtlConfig
from api_etl.auth_provider import get_auth_provider
from api_etl.sources import UBRSource
from core.test_helpers import create_test_interactive_user

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

class UBRSourceTestCase(TestCase):

    @patch("requests.Session.post")
    def test_ubr_source_pull(self, mock_post):
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=page))
            for page in MOCKED_UBR_RESPONSE_DATA
        ]

        user = create_test_interactive_user(username="test_admin")
        source = UBRSource(get_auth_provider('noauth'), pmt_percentile_range=range(0, 3))

        pulled_data = []
        identifiers = []

        for rows, identifier in source.pull():
            pulled_data += rows
            identifiers.append(identifier)

        # Ensure the expected number of records were retrieved
        self.assertEqual(len(pulled_data), 3)
        logging.info(f"Successfully pulled {len(pulled_data)} households")

        # Ensure correct data is present
        self.assertEqual(pulled_data[0]["firstName"], "Alice")
        self.assertEqual(pulled_data[1]["firstName"], "Bob")
        self.assertEqual(pulled_data[2]["firstName"], "Charlie")

        # Ensure identifiers are generated
        self.assertEqual(len(identifiers), 2)
        self.assertTrue(identifiers[0].startswith("batch_209_0_1_"))
        self.assertTrue(identifiers[1].startswith("batch_209_1_2_"))

