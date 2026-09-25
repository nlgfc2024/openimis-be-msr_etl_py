import base64

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.base import AuthProvider, AuthError


class BasicAuthProvider(AuthProvider):
    """
    Auth provider that add basic token authorization header for the request
    """

    def __init__(self, source_type: str = "ubr"):
        self.source_type = source_type

    def get_auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Basic {self._get_token_value()}"}

    def _get_token_value(self):
        config = MsrEtlConfig.get_source_config(self.source_type)
        username = config.get("auth_basic_username")
        password = config.get("auth_basic_password")
        if not username or not password:
            raise AuthError("Basic auth credentials not provided")
        basic_payload = f"{username}:{password}"
        return base64.b64encode(basic_payload.encode("utf-8")).decode("utf-8")
