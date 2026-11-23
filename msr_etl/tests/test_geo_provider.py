from unittest import TestCase
from unittest.mock import Mock

from msr_etl.providers.msr_v3 import MsrV3Provider, ProviderError
from msr_etl.providers.district import DistrictSource


class ProviderTests(TestCase):
    def setUp(self):
        self.session = Mock()
        self.provider = MsrV3Provider({"base_url": "http://localhost:8080", "auth_bearer_token": "fixture"}, self.session)

    def page(self, rows, next_url=None):
        return Mock(status_code=200, json=Mock(return_value={"data": rows, "links": {"next": next_url}}))

    def test_pagination_preserves_false_null_and_publication_fields(self):
        row = {"id": 13, "is_micro": False, "geometry_sha256": None, "revision": 2, "boundary_status": "removed"}
        self.session.get.side_effect = [self.page([row], "?page=2"), self.page([])]
        self.assertEqual(list(self.provider.districts()), [[row], []])
        self.assertFalse(self.session.get.call_args.kwargs["allow_redirects"])

    def test_next_origin_is_checked_before_credentials_are_sent(self):
        self.session.get.return_value = self.page([], "https://external.example/data")
        with self.assertRaisesRegex(ProviderError, "ORIGIN"):
            list(self.provider.districts())
        self.assertEqual(self.session.get.call_count, 1)

    def test_later_page_failure_is_not_empty_success(self):
        self.session.get.side_effect = [self.page([{"id": 13}], "?page=2"), Mock(status_code=503)]
        with self.assertRaisesRegex(ProviderError, "HTTP_503"):
            list(self.provider.districts())

    def test_cycle_and_limits(self):
        self.session.get.return_value = self.page([], self.provider.url)
        with self.assertRaisesRegex(ProviderError, "cycle"):
            list(self.provider.districts())
        self.provider.max_pages = 1
        self.session.get.return_value = self.page([], "?page=2")
        with self.assertRaisesRegex(ProviderError, "PAGE_LIMIT"):
            list(self.provider.districts())

    def test_membership_uses_verified_endpoint(self):
        self.session.get.return_value = Mock(status_code=200, json=Mock(return_value={"data": [], "next_page_url": None, "links": []}))
        self.assertEqual(list(self.provider.membership(265, 13)), [[]])
        self.assertTrue(self.session.get.call_args.args[0].endswith("/api/v2/micro-catchments/265/villages"))

    def test_no_insecure_remote_transport_or_disabled_tls(self):
        for config in ({"base_url": "http://msr.example"}, {"base_url": "https://msr.example", "verify_ssl": False}):
            with self.assertRaises(ProviderError):
                MsrV3Provider(config)

    def test_collector_strips_links_and_preserves_empty_verified_membership(self):
        client = Mock()
        district = {"id": 13, "type": "districts", "district_id": 13, "revision": 1, "geojson_url": "temporary"}
        catchment = {"id": 265, "type": "catchments", "district_id": 13, "revision": 1, "is_micro": True}
        client.districts.return_value = [[district]]
        client.locations.side_effect = [[], [], [], [[catchment]]]
        client.membership.return_value = [[]]
        source = DistrictSource(client, "fixture", 13)
        payload = source.fetch_unit(source.enumerate_units()[0])
        self.assertNotIn("geojson_url", payload["locations"][0])
        self.assertEqual(payload["memberships"][0]["village_ids"], [])

    def test_one_village_cannot_be_assigned_twice(self):
        client = Mock()
        client.districts.return_value = [[{"id": 13}]]
        client.locations.side_effect = [[], [], [], [[{"id": i, "type": "catchments", "district_id": 13, "revision": 1, "is_micro": True} for i in (1, 2)]]]
        client.membership.side_effect = [[[{"id": 88, "catchment_id": 1}]], [[{"id": 88, "catchment_id": 2}]]]
        source = DistrictSource(client, "fixture", 13)
        with self.assertRaisesRegex(ProviderError, "CONFLICT"):
            source.fetch_unit(source.enumerate_units()[0])


class ImportOwnershipTests(TestCase):
    def test_importer_runtime_does_not_import_mapping(self):
        import ast
        from pathlib import Path
        import msr_etl
        for path in Path(msr_etl.__file__).parent.rglob('*.py'):
            if any(part in ('tests','migrations','__pycache__') for part in path.parts):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                names = [node.module or ''] if isinstance(node,ast.ImportFrom) else [item.name for item in node.names] if isinstance(node,ast.Import) else []
                self.assertFalse(any(name=='mapping' or name.startswith('mapping.') for name in names),str(path))
