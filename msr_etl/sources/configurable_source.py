import base64
import logging

import requests

from msr_etl.sources.base import DataSource
from msr_etl.utils import get_timestamped_batch_identifier

logger = logging.getLogger(__name__)


class ConfigurableSource(DataSource):
    """Generic HTTP data source driven entirely by a per-source_type config dict
    (see MsrEtlConfig.get_source_config). Fits a source whose API is a single
    flat request/response with no pagination or bespoke request shaping -
    anything needing that (chunking, multi-step hierarchy walks, etc.) should
    be its own DataSource subclass instead, the way UBRIndividualSource is.
    """

    def __init__(self, config: dict, source_type: str = None, **_unused_filter_kwargs):
        super().__init__()
        if not config or not config.get("base_url"):
            raise self.Error("ConfigurableSource requires 'base_url' in its source config")
        self.config = config
        self.source_type = source_type or "configurable"

    def pull(self):
        records = self.fetch()
        identifier = get_timestamped_batch_identifier(f"batch_{self.source_type}_")
        yield records, identifier

    def fetch(self):
        method = (self.config.get("http_method") or "GET").upper()
        url = self.config["base_url"]
        headers = {**(self.config.get("headers") or {}), **self._auth_header()}
        query_params = self.config.get("query_params") or {}
        timeout = self.config.get("timeout_seconds", 300)
        verify = self.config.get("verify_ssl", True)

        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=query_params if method == "GET" else None,
                json=query_params if method != "GET" else None,
                timeout=timeout,
                verify=verify,
            )
        except requests.exceptions.RequestException as exc:
            raise self.Error(f"Failed to call configured source endpoint '{url}': {exc}") from exc

        if not response.ok:
            logger.error("Configured source request failed: %s %s", response.status_code, response.reason)
            raise self.Error(f"HTTP request failed: {response.status_code}: {response.reason}")

        return self._extract_records(response.json())

    def _extract_records(self, body):
        path = self.config.get("response_records_path")
        if not path:
            return body if isinstance(body, list) else [body]

        value = body
        for key in path.split("."):
            if not isinstance(value, dict):
                raise self.Error(f"response_records_path '{path}' does not resolve against the response body")
            value = value.get(key)

        if value is None:
            return []
        if not isinstance(value, list):
            raise self.Error(f"response_records_path '{path}' did not resolve to a list")
        return value

    def _auth_header(self):
        auth_type = self.config.get("auth_type", "noauth")

        if auth_type == "noauth":
            return {}

        if auth_type == "bearer":
            token = self.config.get("auth_bearer_token")
            if not token:
                raise self.Error("Configured source auth_type is 'bearer' but auth_bearer_token was not set")
            return {"Authorization": f"Bearer {token}"}

        if auth_type == "basic":
            username = self.config.get("auth_basic_username")
            password = self.config.get("auth_basic_password")
            if not username or not password:
                raise self.Error("Configured source auth_type is 'basic' but credentials were not set")
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
            return {"Authorization": f"Basic {token}"}

        raise self.Error(f"Unknown auth_type '{auth_type}' in source config")
