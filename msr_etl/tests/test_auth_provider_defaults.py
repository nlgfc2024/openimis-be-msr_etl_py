from django.test import SimpleTestCase

from msr_etl.auth_provider import get_auth_provider
from msr_etl.auth_provider.base import AuthError
from msr_etl.auth_provider.basicAuthProvider import BasicAuthProvider
from msr_etl.auth_provider.bearerAuthProvider import BearerAuthProvider
from msr_etl.auth_provider.noAuthAuthProvider import NoAuthProvider


class AuthProviderDefaultsTestCase(SimpleTestCase):
    def test_get_auth_provider_uses_noauth_when_explicitly_requested(self):
        provider = get_auth_provider(auth_type="noauth")
        self.assertIsInstance(provider, NoAuthProvider)

    def test_get_auth_provider_defaults_to_noauth_for_unconfigured_source(self):
        # a source_type with no "sources" config entry has no configured
        # auth_type, so get_source_config(...).get("auth_type", "noauth")
        # falls back to noauth - independent of whatever "ubr" is configured to.
        provider = get_auth_provider(source_type="does-not-exist")
        self.assertIsInstance(provider, NoAuthProvider)

    def test_unknown_auth_type_raises(self):
        with self.assertRaises(AuthError):
            get_auth_provider(auth_type="does-not-exist")

    def test_basic_auth_raises_when_credentials_missing(self):
        provider = BasicAuthProvider(source_type="does-not-exist")
        with self.assertRaises(AuthError):
            provider.get_auth_header()

    def test_bearer_auth_raises_when_token_missing(self):
        provider = BearerAuthProvider(source_type="does-not-exist")
        with self.assertRaises(AuthError):
            provider.get_auth_header()
