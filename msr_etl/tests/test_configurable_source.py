from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.sources import ConfigurableSource
from msr_etl.sources.base import DataSource


def mock_json_response(body, ok=True, status_code=200, reason="OK"):
    response = MagicMock()
    response.ok = ok
    response.status_code = status_code
    response.reason = reason
    response.json.return_value = body
    return response


class ConfigurableSourceInitTestCase(SimpleTestCase):

    def test_requires_base_url(self):
        with self.assertRaises(DataSource.Error):
            ConfigurableSource(config={})

    def test_requires_config(self):
        with self.assertRaises(DataSource.Error):
            ConfigurableSource(config=None)


class ConfigurableSourceFetchTestCase(SimpleTestCase):

    @patch("requests.request")
    def test_fetch_returns_plain_list_body(self, mock_request):
        mock_request.return_value = mock_json_response([{"id": 1}, {"id": 2}])
        source = ConfigurableSource(config={"base_url": "https://example.org/records"})

        records = source.fetch()

        self.assertEqual(records, [{"id": 1}, {"id": 2}])
        mock_request.assert_called_once()
        self.assertEqual(mock_request.call_args.args[0], "GET")
        self.assertEqual(mock_request.call_args.args[1], "https://example.org/records")

    @patch("requests.request")
    def test_fetch_extracts_nested_records_path(self, mock_request):
        mock_request.return_value = mock_json_response({"data": {"items": [{"id": 1}]}})
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "response_records_path": "data.items",
        })

        records = source.fetch()

        self.assertEqual(records, [{"id": 1}])

    @patch("requests.request")
    def test_fetch_missing_path_segment_returns_empty_list(self, mock_request):
        mock_request.return_value = mock_json_response({"data": {}})
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "response_records_path": "data.items",
        })

        self.assertEqual(source.fetch(), [])

    @patch("requests.request")
    def test_fetch_non_list_path_raises(self, mock_request):
        mock_request.return_value = mock_json_response({"data": {"items": "not-a-list"}})
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "response_records_path": "data.items",
        })

        with self.assertRaises(DataSource.Error):
            source.fetch()

    @patch("requests.request")
    def test_fetch_raises_on_non_ok_response(self, mock_request):
        mock_request.return_value = mock_json_response({}, ok=False, status_code=500, reason="Server Error")
        source = ConfigurableSource(config={"base_url": "https://example.org/records"})

        with self.assertRaises(DataSource.Error):
            source.fetch()

    @patch("requests.request")
    def test_fetch_wraps_request_exception(self, mock_request):
        import requests
        mock_request.side_effect = requests.exceptions.ConnectionError("boom")
        source = ConfigurableSource(config={"base_url": "https://example.org/records"})

        with self.assertRaises(DataSource.Error):
            source.fetch()

    @patch("requests.request")
    def test_bearer_auth_header_is_sent(self, mock_request):
        mock_request.return_value = mock_json_response([])
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "auth_type": "bearer",
            "auth_bearer_token": "secret-token",
        })

        source.fetch()

        headers = mock_request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-token")

    def test_bearer_auth_without_token_raises(self):
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "auth_type": "bearer",
        })

        with self.assertRaises(DataSource.Error):
            source._auth_header()

    @patch("requests.request")
    def test_basic_auth_header_is_sent(self, mock_request):
        mock_request.return_value = mock_json_response([])
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "auth_type": "basic",
            "auth_basic_username": "user",
            "auth_basic_password": "pass",
        })

        source.fetch()

        headers = mock_request.call_args.kwargs["headers"]
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_unknown_auth_type_raises(self):
        source = ConfigurableSource(config={
            "base_url": "https://example.org/records",
            "auth_type": "does-not-exist",
        })

        with self.assertRaises(DataSource.Error):
            source._auth_header()


class ConfigurableSourcePullTestCase(SimpleTestCase):

    @patch("requests.request")
    def test_pull_yields_records_and_identifier(self, mock_request):
        mock_request.return_value = mock_json_response([{"id": 1}])
        source = ConfigurableSource(
            config={"base_url": "https://example.org/records"},
            source_type="acme",
        )

        batches = list(source.pull())

        self.assertEqual(len(batches), 1)
        records, identifier = batches[0]
        self.assertEqual(records, [{"id": 1}])
        self.assertTrue(identifier.startswith("batch_acme_"))
