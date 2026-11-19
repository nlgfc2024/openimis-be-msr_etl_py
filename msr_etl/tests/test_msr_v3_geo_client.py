from unittest import TestCase
from unittest.mock import Mock, patch

from msr_etl.apps import MsrEtlConfig
from msr_etl.sources.base import DataSource
from msr_etl.sources.msr_v3_geo import MsrV3GeoClient


class MsrV3GeoClientTests(TestCase):
    def setUp(self):
        self.config = patch.object(MsrEtlConfig, "sources", {
            "msr_geo_v3": {"base_url": "http://localhost:8080", "auth_bearer_token": "test-token"},
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        self.session = Mock()
        self.client = MsrV3GeoClient(session=self.session)

    def page(self, rows, next_url=None):
        return Mock(status_code=200, json=Mock(return_value={"data": rows, "links": {"next": next_url}}))

    def test_pagination_preserves_ids_and_publication_metadata(self):
        row = {"id": 13, "code": "213", "geometry": {"revision": 2}}
        self.session.get.side_effect = [self.page([row], "?page=2"), self.page([])]
        self.assertEqual(list(self.client.districts()), [[row], []])
        first, second = self.session.get.call_args_list
        self.assertEqual(first.kwargs["params"], {"type": "districts"})
        self.assertEqual(first.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertFalse(first.kwargs["allow_redirects"])
        self.assertEqual(second.args[0], "http://localhost:8080/api/v3/geo-locations?page=2")

    def test_catchments_use_msr_id(self):
        self.session.get.return_value = self.page([])
        list(self.client.catchments(13))
        self.assertEqual(self.session.get.call_args.kwargs["params"], {"type": "catchments", "district_id": "13"})

    def test_rejects_external_pagination_before_sending_credentials(self):
        self.session.get.return_value = self.page([], "https://other.example/api/v3/geo-locations")
        with self.assertRaises(DataSource.Error):
            list(self.client.districts())
        self.assertEqual(self.session.get.call_count, 1)

    def test_rejects_pagination_cycle(self):
        self.session.get.return_value = self.page([], self.client.url)
        with self.assertRaisesRegex(DataSource.Error, "cycle"):
            list(self.client.districts())

    def test_does_not_treat_failure_as_removal(self):
        self.session.get.return_value = Mock(status_code=503)
        with self.assertRaisesRegex(DataSource.Error, "HTTP 503"):
            list(self.client.districts())

    def test_rejects_unknown_envelope(self):
        self.session.get.return_value = Mock(status_code=200, json=Mock(return_value={"results": []}))
        with self.assertRaisesRegex(DataSource.Error, "data array"):
            list(self.client.districts())

    def test_requires_explicit_district(self):
        with self.assertRaises(DataSource.Error):
            self.client.catchments(None)

    def test_membership_uses_verified_v2_endpoint_and_pagination(self):
        self.session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value={"data": [{"id": 40, "catchment_id": 667}], "links": [{"label": "Next", "url": "?page=2"}], "next_page_url": "?page=2"})),
            Mock(status_code=200, json=Mock(return_value={"data": [], "next_page_url": None})),
        ]
        self.assertEqual(list(self.client.membership(667, 13)), [[{"id": 40, "catchment_id": 667}], []])
        self.assertEqual(self.session.get.call_args.args[0], "http://localhost:8080/api/v2/micro-catchments/667/villages?page=2")
