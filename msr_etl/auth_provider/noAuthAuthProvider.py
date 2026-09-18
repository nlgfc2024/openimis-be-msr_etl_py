from msr_etl.auth_provider.base import AuthProvider


class NoAuthProvider(AuthProvider):
    """
    Implementation of AuthProvider Interface that does not provide any authorization
    """

    def __init__(self, source_type: str = "ubr"):
        self.source_type = source_type

    def get_auth_header(self) -> dict[str, str]:
        return {}
