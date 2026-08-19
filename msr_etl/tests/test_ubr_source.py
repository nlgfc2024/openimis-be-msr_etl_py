import io
import json
import logging
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, TestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.sources import UBRIndividualSource, UBRLocationSource
import requests


def mock_streamed_json_response(data, ok=True):
    # fetch_households reads res.raw via json.load(), not res.json()
    return MagicMock(ok=ok, raw=io.BytesIO(json.dumps(data).encode()))

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


class UBRIndividualSourceTestCase(SimpleTestCase):

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

    @patch("time.sleep")
    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_ubr_source_pull(self, mock_location_filter, mock_post, mock_sleep):
        """
        Test that UBRIndividualSource.pull iterates over districts and TAs,
        and makes one API call per TA with correct params.
        """
        # Setup the filter mock to return different codes based on the filter arguments
        def filter_side_effect(*args, **kwargs):
            if kwargs.get("type") == "R":
                # Districts
                mock_qs = MagicMock()
                mock_qs.values_list.return_value = self.mocked_district_codes
                return mock_qs
            elif kwargs.get("type") == "D":
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

        # Mock the API responses for each TA.
        mock_post.side_effect = [
            mock_streamed_json_response(self.mocked_ubr_response_data[0]),
            mock_streamed_json_response(self.mocked_ubr_response_data[1]),
        ]

        source = UBRIndividualSource(
            get_auth_provider('noauth'),
            pmt_percentile_range=range(1, 3),
            wealth_quintiles=[1, 2, 3, 4],
            classification=[4, 5],
            gender="Female",
            min_age=18,
            max_age=60,
        )

        pulled_data = []
        identifiers = []

        for rows, identifier in source.pull():
            pulled_data += rows
            identifiers.append(identifier)

        # There should be 2 API calls (one per TA)
        self.assertEqual(mock_post.call_count, 2)
        # Each call should have correct params
        for i, call in enumerate(mock_post.call_args_list):
            _, kwargs = call
            params = kwargs["params"]
            self.assertIn(params["district_code"], self.mocked_district_codes)
            self.assertIn(params["traditional_authority_code"], self.mocked_ta_codes)
            self.assertEqual(params["lower_percentile_category"], '1')
            self.assertEqual(params["upper_percentile_category"], '2')
            self.assertEqual(params["wealth_quintile"], "4,5")
            self.assertEqual(params["gender"], "Female")
            self.assertEqual(params["minAge"], "18")
            self.assertEqual(params["maxAge"], "60")

        # All individuals should be present
        self.assertEqual(len(pulled_data), 2)
        self.assertEqual(pulled_data[0]["firstName"], "Alice")
        self.assertEqual(pulled_data[1]["firstName"], "Bob")

        # Identifiers should be unique per TA and match the code structure
        self.assertEqual(len(set(identifiers)), 2)
        for ta_code in self.mocked_ta_codes:
            self.assertTrue(any(f"batch_101_{ta_code}_" in ident for ident in identifiers))

        logging.info(f"Successfully pulled {len(pulled_data)} households for all TAs")

    @patch("time.sleep")
    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_ubr_source_pull_with_location_filters(self, mock_location_filter, mock_post, mock_sleep):
        def filter_side_effect(*args, **kwargs):
            mock_qs = MagicMock()
            mock_qs.exists.return_value = True
            return mock_qs

        mock_location_filter.side_effect = filter_side_effect
        mock_post.return_value = mock_streamed_json_response(self.mocked_ubr_response_data[0])

        source = UBRIndividualSource(
            get_auth_provider('noauth'),
            pmt_percentile_range=range(1, 3),
            district="101",
            ta="10101",
            gvh="1010101",
            village="10101001",
            wealth_quintiles=[2, 5],
            classification=[3],
            gender="Male",
            min_age=5,
            max_age=17,
        )

        pulled_data = []
        identifiers = []
        for rows, identifier in source.pull():
            pulled_data += rows
            identifiers.append(identifier)

        self.assertEqual(mock_post.call_count, 1)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["params"]["district_code"], "101")
        self.assertEqual(kwargs["params"]["traditional_authority_code"], "10101")
        self.assertEqual(kwargs["params"]["group_village_head_code"], "1010101")
        self.assertEqual(kwargs["params"]["village_code"], "10101001")
        self.assertEqual(kwargs["params"]["lower_percentile_category"], "1")
        self.assertEqual(kwargs["params"]["upper_percentile_category"], "2")
        self.assertEqual(kwargs["params"]["wealth_quintile"], "3")
        self.assertEqual(kwargs["params"]["gender"], "Male")
        self.assertEqual(kwargs["params"]["minAge"], "5")
        self.assertEqual(kwargs["params"]["maxAge"], "17")
        self.assertEqual(len(pulled_data), 1)
        self.assertTrue(identifiers[0].startswith("batch_101_10101_"))
        mock_location_filter.assert_any_call(
            code="1010101",
            parent__code="10101",
            parent__type="D",
            parent__validity_to__isnull=True,
            type="W",
            validity_to__isnull=True,
        )
        mock_location_filter.assert_any_call(
            code="10101001",
            parent__code="1010101",
            parent__parent__code="10101",
            parent__parent__type="D",
            parent__parent__validity_to__isnull=True,
            parent__type="W",
            parent__validity_to__isnull=True,
            type="V",
            validity_to__isnull=True,
        )

    def test_fetch_households_streams_and_closes_response(self):
        source = UBRIndividualSource(get_auth_provider('noauth'))
        session = MagicMock()
        response = mock_streamed_json_response({
            "error_occurred": False,
            "targeting_data": [{"id": 1}],
        })
        session.post.return_value = response

        rows = source.fetch_households(
            session=session,
            url=self.url,
            headers=self.headers,
            district_code="101",
            ta_code="10101",
        )

        self.assertEqual(rows, [{"id": 1}])
        self.assertTrue(session.post.call_args.kwargs["stream"])
        self.assertTrue(response.raw.decode_content)
        response.close.assert_called_once()

    def test_fetch_households_closes_response_on_error(self):
        source = UBRIndividualSource(get_auth_provider('noauth'))
        session = MagicMock()
        response = MagicMock(ok=False, status_code=502, reason="Bad Gateway")
        session.post.return_value = response

        with self.assertRaises(source.Error):
            source.fetch_households(
                session=session,
                url=self.url,
                headers=self.headers,
                district_code="101",
                ta_code="10101",
            )

        response.close.assert_called_once()

    def test_fetch_households_ssl_error_has_actionable_message(self):
        source = UBRIndividualSource(get_auth_provider('noauth'))
        session = MagicMock()
        session.post.side_effect = requests.exceptions.SSLError("self-signed certificate in certificate chain")

        with self.assertRaisesRegex(
            source.Error,
            "source_ca_bundle_path.*source_verify_ssl",
        ):
            source.fetch_households(
                session=session,
                url=self.url,
                headers=self.headers,
                district_code="101",
                ta_code="10101",
            )

    @patch.object(MsrEtlConfig, "source_percentile_chunk_size", 10)
    def test_percentile_chunks_are_inclusive_and_non_overlapping(self):
        source = UBRIndividualSource(
            get_auth_provider('noauth'),
            pmt_percentile_range=range(0, 101),
        )

        chunks = [
            (chunk.start, chunk.stop - 1)
            for chunk in source._iter_percentile_chunks()
        ]

        self.assertEqual(chunks, [
            (0, 9),
            (10, 19),
            (20, 29),
            (30, 39),
            (40, 49),
            (50, 59),
            (60, 69),
            (70, 79),
            (80, 89),
            (90, 99),
            (100, 100),
        ])

    @patch.object(MsrEtlConfig, "source_percentile_chunk_delay_seconds", 0)
    @patch.object(MsrEtlConfig, "source_percentile_chunk_size", 10)
    @patch("time.sleep")
    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_pull_streams_chunks_and_deduplicates_households(
        self,
        mock_location_filter,
        mock_post,
        mock_sleep,
    ):
        location_qs = MagicMock()
        location_qs.exists.return_value = True
        mock_location_filter.return_value = location_qs
        mock_post.side_effect = [
            mock_streamed_json_response({
                "error_occurred": False,
                "targeting_data": [{"id": 1}, {"id": 2}],
            }),
            mock_streamed_json_response({
                "error_occurred": False,
                "targeting_data": [{"id": 2}, {"id": 3}],
            }),
            mock_streamed_json_response({
                "error_occurred": False,
                "targeting_data": [{"id": 4}],
            }),
        ]

        source = UBRIndividualSource(
            get_auth_provider('noauth'),
            pmt_percentile_range=range(0, 21),
            district="101",
            ta="10101",
        )

        results = list(source.pull())

        self.assertEqual(
            [[row["id"] for row in rows] for rows, _ in results],
            [[1, 2], [3], [4]],
        )
        self.assertEqual(
            [
                (
                    call.kwargs["params"]["lower_percentile_category"],
                    call.kwargs["params"]["upper_percentile_category"],
                )
                for call in mock_post.call_args_list
            ],
            [("0", "9"), ("10", "19"), ("20", "20")],
        )
        self.assertTrue(results[0][1].startswith("batch_101_10101_pmt_0_9_"))
        self.assertTrue(results[1][1].startswith("batch_101_10101_pmt_10_19_"))
        self.assertTrue(results[2][1].startswith("batch_101_10101_pmt_20_20_"))
        mock_sleep.assert_called_once_with(5)

    @patch.object(MsrEtlConfig, "source_percentile_chunk_delay_seconds", 0)
    @patch.object(MsrEtlConfig, "source_percentile_chunk_size", 10)
    @patch("requests.Session.post")
    @patch("location.models.Location.objects.filter")
    def test_pull_error_identifies_failed_percentile_chunk(
        self,
        mock_location_filter,
        mock_post,
    ):
        location_qs = MagicMock()
        location_qs.exists.return_value = True
        mock_location_filter.return_value = location_qs
        mock_post.side_effect = [
            mock_streamed_json_response({
                "error_occurred": False,
                "targeting_data": [{"id": 1}],
            }),
            requests.exceptions.ReadTimeout("UBR read timed out"),
        ]
        source = UBRIndividualSource(
            get_auth_provider('noauth'),
            pmt_percentile_range=range(0, 21),
            district="101",
            ta="10101",
        )

        with self.assertRaisesRegex(
            source.Error,
            "percentile chunk 10-19 failed",
        ):
            list(source.pull())


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

        cls.mocked_gvhs_response = [
            {
                "error_occurred": False,
                "total_records": 2,
                "geo_locations": [
                    {"geo_location_code": "1010101", "geo_location_name": "GVH A", "parent_geo_location_code": "10101", "geo_location_type_id": 4, "geo_location_type_name": "GROUP_VILLAGE_HEAD"},
                    {"geo_location_code": "1010102", "geo_location_name": "GVH B", "parent_geo_location_code": "10101", "geo_location_type_id": 4, "geo_location_type_name": "GROUP_VILLAGE_HEAD"},
                ],
            },
            {
                "error_occurred": False,
                "total_records": 2,
                "geo_locations": [
                    {"geo_location_code": "1020101", "geo_location_name": "GVH C", "parent_geo_location_code": "10201", "geo_location_type_id": 4, "geo_location_type_name": "GROUP_VILLAGE_HEAD"},
                    {"geo_location_code": "1020102", "geo_location_name": "GVH D", "parent_geo_location_code": "10201", "geo_location_type_id": 4, "geo_location_type_name": "GROUP_VILLAGE_HEAD"},
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

    @patch("msr_etl.sources.ubr_source.time.sleep")
    @patch("requests.Session.post")
    def test_pull(self, mock_post, mock_sleep):
        # Mock the API responses for districts, TAs, and villages
        mock_post.side_effect = [
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_districts_response)),  # Districts
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[0])),  # TAs for District 101
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_gvhs_response[0])),  # GVHs for District 101
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0])),  # Villages for District 101
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[1])),  # TAs for District 102
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_gvhs_response[1])),  # GVHs for District 102
            MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[1])),  # Villages for District 102
        ]

        # Call the pull method
        source = UBRLocationSource(get_auth_provider('noauth'))
        results = list(source.pull())

        # Assertions
        self.assertEqual(len(results), 8)  # 2 districts, 2 TAs, 2 GVHs, 2 villages
        self.assertEqual(results[0][0]["data_type"], "D")
        self.assertEqual(results[1][0]["data_type"], "T")
        self.assertEqual(results[2][0]["data_type"], "G")
        self.assertEqual(results[3][0]["data_type"], "V")
        self.assertEqual(results[4][0]["data_type"], "D")
        self.assertEqual(results[5][0]["data_type"], "T")
        self.assertEqual(results[6][0]["data_type"], "G")
        self.assertEqual(results[7][0]["data_type"], "V")

        # Check the number of records in each result
        self.assertEqual(len(results[0][0]["data"]), 1)  # District 101
        self.assertEqual(len(results[1][0]["data"]), 2)  # TAs for District 101
        self.assertEqual(len(results[2][0]["data"]), 2)  # GVHs for District 101
        self.assertEqual(len(results[3][0]["data"]), 2)  # Villages for District 101
        self.assertEqual(len(results[4][0]["data"]), 1)  # District 102
        self.assertEqual(len(results[5][0]["data"]), 2)  # TAs for District 102
        self.assertEqual(len(results[6][0]["data"]), 2)  # GVHs for District 102
        self.assertEqual(len(results[7][0]["data"]), 2)  # Villages for District 102
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("msr_etl.sources.ubr_source.time.sleep")
    @patch("requests.Session.post")
    def test_pull_skips_district_and_children_when_no_tas(
        self,
        mock_post,
        mock_sleep,
    ):
        empty_tas_response = {
            "error_occurred": False,
            "total_records": 0,
            "geo_locations": [],
        }
        mock_post.side_effect = [
            MagicMock(
                ok=True,
                json=MagicMock(return_value=self.mocked_districts_response),
            ),
            MagicMock(ok=True, json=MagicMock(return_value=empty_tas_response)),
            MagicMock(
                ok=True,
                json=MagicMock(return_value=self.mocked_tas_response[1]),
            ),
            MagicMock(
                ok=True,
                json=MagicMock(return_value=self.mocked_gvhs_response[1]),
            ),
            MagicMock(
                ok=True,
                json=MagicMock(return_value=self.mocked_villages_response[1]),
            ),
        ]

        results = list(UBRLocationSource(get_auth_provider("noauth")).pull())

        self.assertEqual(
            [batch["data_type"] for batch, _identifier in results],
            ["D", "T", "G", "V"],
        )
        self.assertEqual(results[0][0]["data"][0]["geo_location_code"], "102")
        requested_params = [
            call.kwargs["json"] for call in mock_post.call_args_list
        ]
        self.assertNotIn(
            {"geo_location_type_id": 4, "district_code": "101"},
            requested_params,
        )
        self.assertNotIn(
            {"geo_location_type_id": 11, "district_code": "101"},
            requested_params,
        )
        mock_sleep.assert_called_once_with(30)

    def test_list_districts_scoped_skips_api_call(self):
        source = UBRLocationSource(get_auth_provider("noauth"), district="101")

        with patch("requests.Session.post") as mock_post:
            districts = source.list_districts()

        mock_post.assert_not_called()
        self.assertEqual(districts, [{"geo_location_code": "101"}])

    @patch("requests.Session.post")
    def test_list_districts_unscoped_fetches_all(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_districts_response))
        source = UBRLocationSource(get_auth_provider("noauth"))

        districts = source.list_districts()

        self.assertEqual(len(districts), 2)

    @patch("requests.Session.post")
    def test_fetch_unit_narrows_ta_by_scope(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_tas_response[0]))
        source = UBRLocationSource(get_auth_provider("noauth"), district="101", ta="10102")

        result = source.fetch_unit("101", "TA")

        self.assertEqual([row["geo_location_code"] for row in result["data"]], ["10102"])

    @patch("requests.Session.post")
    def test_fetch_unit_narrows_gvh_by_ta_and_gvh_scope(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_gvhs_response[0]))
        source = UBRLocationSource(get_auth_provider("noauth"), district="101", ta="10101", gvh="1010102")

        result = source.fetch_unit("101", "GVH")

        self.assertEqual([row["geo_location_code"] for row in result["data"]], ["1010102"])

    @patch("requests.Session.post")
    def test_fetch_unit_ta_scope_does_not_narrow_gvh(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_gvhs_response[0]))
        source = UBRLocationSource(get_auth_provider("noauth"), district="101", ta="10101")

        result = source.fetch_unit("101", "GVH")

        self.assertEqual(len(result["data"]), 2)

    @patch("requests.Session.post")
    def test_fetch_unit_narrows_village_by_ta_prefix_without_gvh(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0]))
        source = UBRLocationSource(get_auth_provider("noauth"), district="101", ta="10101")

        result = source.fetch_unit("101", "VILLAGE")

        self.assertEqual(len(result["data"]), 2)

        other_ta_source = UBRLocationSource(get_auth_provider("noauth"), district="101", ta="10102")
        other_result = other_ta_source.fetch_unit("101", "VILLAGE")
        self.assertEqual(other_result["data"], [])

    @patch("requests.Session.post")
    def test_fetch_unit_narrows_village_by_gvh_scope(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0]))
        source = UBRLocationSource(get_auth_provider("noauth"), district="101", ta="10101", gvh="does-not-match")

        result = source.fetch_unit("101", "VILLAGE")

        self.assertEqual(result["data"], [])

    @patch("requests.Session.post")
    def test_fetch_unit_narrows_village_by_village_scope(self, mock_post):
        mock_post.return_value = MagicMock(ok=True, json=MagicMock(return_value=self.mocked_villages_response[0]))
        source = UBRLocationSource(
            get_auth_provider("noauth"), district="101", ta="10101", gvh="10101", village="101010102",
        )

        result = source.fetch_unit("101", "VILLAGE")

        self.assertEqual([row["geo_location_code"] for row in result["data"]], ["101010102"])
