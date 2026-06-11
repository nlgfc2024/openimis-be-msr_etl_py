import base64

from api_etl.apps import ApiEtlConfig
from api_etl.auth_provider.base import AuthProvider, AuthError


class BasicAuthProvider(AuthProvider):
    """
    Auth provider that add basic token authorization header for the request
    """

    def get_auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Basic {BasicAuthProvider._get_token_value()}"}

    @staticmethod
    def _get_token_value():
        if not ApiEtlConfig.auth_basic_username or not ApiEtlConfig.auth_basic_password:
            raise AuthError("Basic auth credentials not provided")
        basic_payload = f"{ApiEtlConfig.auth_basic_username}:{ApiEtlConfig.auth_basic_password}"
        return base64.b64encode(basic_payload.encode("utf-8")).decode("utf-8")
