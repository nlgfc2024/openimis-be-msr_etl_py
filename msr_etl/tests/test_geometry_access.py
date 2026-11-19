import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.geometry_access import load_geometry, safe_geojson


POLYGON = {"type": "Polygon", "coordinates": [[[33, -14], [34, -14], [34, -13], [33, -14]]]}


def response(status=200, body=None, raw=b""):
    value = MagicMock(status_code=status)
    value.__enter__.return_value = value
    value.json.return_value = body
    value.iter_content.return_value = [raw]
    return value


class GeometryAccessTests(SimpleTestCase):
    def setUp(self):
        self.config = patch.object(MsrEtlConfig, "sources", {"msr_geo_v3": {
            "enabled": True, "auth_bearer_token": "not-a-real-token"}})
        self.config.start()
        self.addCleanup(self.config.stop)
        client_patch = patch("msr_etl.geometry_access.MsrV3GeoClient")
        self.client = client_patch.start().return_value
        self.addCleanup(client_patch.stop)
        self.client.url = "http://localhost:8080/api/v3/geo-locations"
        self.client.auth.get_auth_header.return_value = {"Authorization": "Bearer not-a-real-token"}
        self.ref = SimpleNamespace(source_instance="msr-malawi", msr_type="districts", msr_id=13,
                                   revision=1, geometry_relation="boundary", geometry_source="pilot")
        self.raw = json.dumps(POLYGON).encode()
        self.row = {"type": "districts", "id": 13, "revision": 2, "boundary_status": "available",
                    "geometry_relation": "boundary", "source": "pilot", "geojson_url": "http://localhost:8080/geo-files/12?signature=test",
                    "geometry_sha256": hashlib.sha256(self.raw).hexdigest()}

    def test_geometry_is_validated_and_service_token_not_forwarded_to_file(self):
        self.client.session.get.side_effect = [response(body={"data": self.row}), response(raw=self.raw)]
        result = load_geometry(self.ref)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["revision"], "2")
        self.assertNotIn("Authorization", self.client.session.get.call_args.kwargs["headers"])
        self.assertNotIn("signature", json.dumps(result))

    def test_external_url_and_redirect_are_not_followed(self):
        self.client.session.get.return_value = response(body={"data": {**self.row, "geojson_url": "http://other.example/geo-files/12"}})
        self.assertEqual(load_geometry(self.ref)["status"], "delivery_unavailable")
        self.assertEqual(self.client.session.get.call_count, 1)
        self.assertFalse(self.client.session.get.call_args.kwargs["allow_redirects"])

    def test_expired_link_refreshed_once(self):
        self.client.session.get.side_effect = [response(body={"data": self.row}), response(403),
            response(body={"data": self.row}), response(raw=self.raw)]
        self.assertEqual(load_geometry(self.ref)["status"], "available")
        self.assertEqual(self.client.session.get.call_count, 4)

    def test_removal_returns_no_layer_without_downloading(self):
        self.client.session.get.return_value = response(body={"data": {**self.row, "boundary_status": "unavailable"}})
        result = load_geometry(self.ref)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["geojson"])
        self.assertEqual(self.client.session.get.call_count, 1)

    def test_rejects_old_revision_and_hash_mismatch(self):
        self.client.session.get.return_value = response(body={"data": {**self.row, "revision": 0}})
        self.assertEqual(load_geometry(self.ref)["status"], "delivery_unavailable")
        self.client.session.get.side_effect = [response(body={"data": self.row}), response(raw=b"wrong")]
        self.assertEqual(load_geometry(self.ref)["status"], "integrity_failure")

    def test_byte_limit_is_enforced(self):
        self.client.session.get.side_effect = [response(body={"data": self.row}), response(raw=self.raw)]
        with patch("msr_etl.geometry_access.MAX_BYTES", 2):
            self.assertEqual(load_geometry(self.ref)["status"], "geometry_too_large")

    def test_properties_removed_and_invalid_coordinates_rejected(self):
        result = safe_geojson({"type": "Feature", "properties": {"secret": "not returned"}, "geometry": POLYGON})
        self.assertEqual(result["features"][0]["properties"], {})
        with self.assertRaises(ValueError):
            safe_geojson({"type": "Point", "coordinates": [0, 0]})
        with self.assertRaises(ValueError):
            safe_geojson({"type": "Polygon", "coordinates": [[[181, 0]] * 4]})

    def test_unconfigured_source_does_not_call_network(self):
        with patch.object(MsrEtlConfig, "sources", {}):
            self.assertEqual(load_geometry(self.ref)["status"], "source_unconfigured")
        self.client.session.get.assert_not_called()
