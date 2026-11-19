from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.base import AuthProvider, AuthError


class TokenAuthProvider(AuthProvider):
    """
    Auth provider for APIs that take "Authorization: Token <key>", e.g. KoboToolbox
    """

    def __init__(self, source_type: str = "ubr"):
        self.source_type = source_type

    def get_auth_header(self) -> dict[str, str]:
        token = MsrEtlConfig.get_source_config(self.source_type).get("auth_token")
        if not token:
            raise AuthError("API token not provided")
        return {"Authorization": f"Token {token}"}
