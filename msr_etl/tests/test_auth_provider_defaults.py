from django.test import TestCase

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.auth_provider.base import AuthError
from msr_etl.auth_provider.noAuthAuthProvider import NoAuthProvider
from msr_etl.auth_provider.basicAuthProvider import BasicAuthProvider


class AuthProviderDefaultsTestCase(TestCase):
    def test_default_auth_type_is_noauth(self):
        self.assertEqual(MsrEtlConfig.auth_type, "noauth")

    def test_get_auth_provider_uses_noauth_by_default(self):
        provider = get_auth_provider()
        self.assertIsInstance(provider, NoAuthProvider)

    def test_basic_auth_raises_when_credentials_missing(self):
        provider = BasicAuthProvider()
        with self.assertRaises(AuthError):
            provider.get_auth_header()
