from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.sources.base import DataSource
from msr_etl.sources.http import (
    get_ssl_verify_setting,
    get_user_agent_header,
    post_with_resilience,
    resolve_source_url,
)


class SourceHttpTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)

    def test_user_agent_header_only_when_configured(self):
        MsrEtlConfig.sources = {"pwp": {"user_agent": "msr-etl-connector"}}
        self.assertEqual(get_user_agent_header("pwp"), {"User-Agent": "msr-etl-connector"})
        MsrEtlConfig.sources = {"pwp": {}}
        self.assertEqual(get_user_agent_header("pwp"), {})

    def test_resolve_source_url_appends_endpoint_path(self):
        MsrEtlConfig.sources = {"pwp": {"base_url": "https://pwp.example.org/api/"}}
        self.assertEqual(
            resolve_source_url("pwp", "https://default", "/graphql"),
            "https://pwp.example.org/api/graphql",
        )

    def test_ssl_verify_follows_source_config(self):
        MsrEtlConfig.sources = {"pwp": {"verify_ssl": False}}
        self.assertFalse(get_ssl_verify_setting("pwp"))
        MsrEtlConfig.sources = {"pwp": {"ca_bundle_path": "/does/not/exist.pem"}}
        with self.assertRaises(DataSource.Error):
            get_ssl_verify_setting("pwp")

    def test_post_errors_name_the_source_type(self):
        MsrEtlConfig.sources = {"pwp": {}}
        session = MagicMock()
        session.post.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaisesRegex(DataSource.Error, "Failed to call 'pwp' endpoint"):
            post_with_resilience("pwp", session, "https://pwp.example.org/api/graphql", {})

    def test_post_uses_source_timeout_and_ssl(self):
        MsrEtlConfig.sources = {"pwp": {"timeout_seconds": 42, "verify_ssl": False}}
        session = MagicMock()
        post_with_resilience("pwp", session, "https://x", {"A": "b"}, json={})
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["timeout"], 42)
        self.assertFalse(kwargs["verify"])
        self.assertEqual(kwargs["headers"], {"A": "b"})


class UBRSourceUserAgentTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)

    def test_ubr_headers_include_configured_user_agent(self):
        from msr_etl.auth_provider import get_auth_provider
        from msr_etl.sources import UBRIndividualSource

        MsrEtlConfig.sources = {"ubr": {"user_agent": "msr-etl-connector", "headers": {"Accept": "application/json"}}}
        source = UBRIndividualSource(get_auth_provider("noauth"))
        self.assertEqual(
            source._get_headers(),
            {"Accept": "application/json", "User-Agent": "msr-etl-connector"},
        )


@patch("msr_etl.sources.http.create_retry_session", return_value=MagicMock())
@patch("msr_etl.sources.http.post_with_resilience")
class OpenimisJwtAuthProviderTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)
        MsrEtlConfig.sources = {
            "pwp": {
                "base_url": "https://pwp.example.org/api/graphql",
                "auth_type": "openimis_jwt",
                "auth_basic_username": "svc_jobsnow_etl",
                "auth_basic_password": "secret",
                "user_agent": "msr-etl-connector",
            },
        }

    def _provider(self):
        from msr_etl.auth_provider import get_auth_provider
        return get_auth_provider(source_type="pwp")

    @staticmethod
    def _response(body, ok=True, status_code=200):
        return MagicMock(ok=ok, status_code=status_code, json=MagicMock(return_value=body))

    def test_logs_in_and_returns_bearer_header(self, mock_post, _session):
        mock_post.return_value = self._response({"data": {"tokenAuth": {"token": "jwt-1"}}})

        header = self._provider().get_auth_header()

        self.assertEqual(header, {"Authorization": "Bearer jwt-1"})
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "pwp")
        self.assertEqual(args[2], "https://pwp.example.org/api/graphql")
        self.assertEqual(args[3]["User-Agent"], "msr-etl-connector")
        self.assertEqual(kwargs["json"]["variables"], {"username": "svc_jobsnow_etl", "password": "secret"})

    def test_reuses_token_until_invalidated(self, mock_post, _session):
        mock_post.side_effect = [
            self._response({"data": {"tokenAuth": {"token": "jwt-1"}}}),
            self._response({"data": {"tokenAuth": {"token": "jwt-2"}}}),
        ]
        provider = self._provider()

        provider.get_auth_header()
        self.assertEqual(provider.get_auth_header(), {"Authorization": "Bearer jwt-1"})
        self.assertEqual(mock_post.call_count, 1)

        provider.invalidate()
        self.assertEqual(provider.get_auth_header(), {"Authorization": "Bearer jwt-2"})
        self.assertEqual(mock_post.call_count, 2)

    def test_graphql_errors_raise_auth_error(self, mock_post, _session):
        from msr_etl.auth_provider import AuthError
        mock_post.return_value = self._response({"data": {"tokenAuth": None}, "errors": [{"message": "Please enter valid credentials"}]})

        with self.assertRaisesRegex(AuthError, "Please enter valid credentials"):
            self._provider().get_auth_header()

    def test_http_failure_raises_auth_error(self, mock_post, _session):
        from msr_etl.auth_provider import AuthError
        mock_post.return_value = self._response({}, ok=False, status_code=403)

        with self.assertRaisesRegex(AuthError, "HTTP 403"):
            self._provider().get_auth_header()

    def test_missing_credentials_raise_before_any_call(self, mock_post, _session):
        from msr_etl.auth_provider import AuthError
        MsrEtlConfig.sources["pwp"]["auth_basic_password"] = ""

        with self.assertRaisesRegex(AuthError, "service user credentials"):
            self._provider().get_auth_header()
        mock_post.assert_not_called()
