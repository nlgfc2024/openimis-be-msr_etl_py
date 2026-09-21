from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.base import AuthProvider, AuthError


class BearerAuthProvider(AuthProvider):
    """
    Auth provider that add bearer token authorization header for the request
    """

    def __init__(self, source_type: str = "ubr"):
        self.source_type = source_type

    def get_auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token_value()}"}

    def _get_token_value(self):
        token = MsrEtlConfig.get_source_config(self.source_type).get("auth_bearer_token")
        if not token:
            raise AuthError("Bearer token not provided")
        return token
