from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.base import AuthError, AuthProvider

TOKEN_AUTH_MUTATION = """
mutation($username: String!, $password: String!) {
  tokenAuth(username: $username, password: $password) { token }
}
"""


class OpenimisJwtAuthProvider(AuthProvider):
    """
    Logs in to a remote openIMIS GraphQL endpoint (the source's base_url) with the
    configured service user via tokenAuth, and reuses the JWT until invalidate().
    """

    def __init__(self, source_type: str = "ubr"):
        self.source_type = source_type
        self._token = None

    def get_auth_header(self) -> dict[str, str]:
        if not self._token:
            self._token = self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def invalidate(self):
        """Drop the cached token so the next request logs in again, e.g. after a 401."""
        self._token = None

    def _login(self):
        from msr_etl.sources.http import create_retry_session, get_user_agent_header, post_with_resilience

        config = MsrEtlConfig.get_source_config(self.source_type)
        url = str(config.get("base_url") or "").strip()
        username = config.get("auth_basic_username")
        password = config.get("auth_basic_password")
        if not url:
            raise AuthError(f"openIMIS JWT auth for source '{self.source_type}' requires base_url")
        if not username or not password:
            raise AuthError(f"openIMIS JWT auth for source '{self.source_type}' requires service user credentials")

        response = post_with_resilience(
            self.source_type,
            create_retry_session(self.source_type),
            url,
            {"Content-Type": "application/json", **get_user_agent_header(self.source_type)},
            json={"query": TOKEN_AUTH_MUTATION, "variables": {"username": username, "password": password}},
        )
        if not response.ok:
            raise AuthError(f"openIMIS login for source '{self.source_type}' failed: HTTP {response.status_code}")

        body = response.json()
        token = ((body.get("data") or {}).get("tokenAuth") or {}).get("token")
        if not token:
            errors = "; ".join(str(e.get("message")) for e in body.get("errors") or []) or "no token returned"
            raise AuthError(f"openIMIS login for source '{self.source_type}' failed: {errors}")
        return token
