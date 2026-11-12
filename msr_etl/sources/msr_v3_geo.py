"""Transport for MSR's publication API, separate from legacy household imports.

Rows remain unmodified until the publication contract is mapped by a dedicated
adapter. Never route these rows through the legacy location writer.
"""
from urllib.parse import urljoin, urlsplit

import requests

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.bearerAuthProvider import BearerAuthProvider
from msr_etl.sources.base import DataSource
from msr_etl.sources.http import (
    create_retry_session, get_ssl_verify_setting, get_timeout_seconds,
)


class MsrV3GeoClient:
    def __init__(self, source_type="msr_geo_v3", session=None):
        self.source_type = source_type
        config = MsrEtlConfig.get_source_config(source_type)
        base = str(config.get("base_url") or "").strip()
        parsed = urlsplit(base)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise DataSource.Error("MSR v3 requires a valid backend base_url without credentials or query")
        self.url = base.rstrip("/") + "/api/v3/geo-locations"
        self.session = session if session is not None else create_retry_session(source_type)
        self.auth = BearerAuthProvider(source_type=source_type)

    def districts(self):
        return self._pages({"type": "districts"})

    def catchments(self, district_id):
        if district_id is None or not str(district_id).strip():
            raise DataSource.Error("An MSR district ID is required; do not use a CoreMIS ID")
        return self._pages({"type": "catchments", "district_id": str(district_id)})

    def _pages(self, params):
        url, visited = self.url, set()
        while url:
            # Never forward a Bearer token to a host/path supplied by pagination.
            parsed, expected = urlsplit(url), urlsplit(self.url)
            if (parsed.scheme, parsed.netloc, parsed.path) != (
                    expected.scheme, expected.netloc, expected.path):
                raise DataSource.Error("MSR pagination must stay on the configured geo-locations endpoint")
            if url in visited:
                raise DataSource.Error("MSR pagination cycle detected")
            visited.add(url)
            try:
                response = self.session.get(
                    url, params=params, headers={"Accept": "application/json", **self.auth.get_auth_header()},
                    timeout=get_timeout_seconds(self.source_type),
                    verify=get_ssl_verify_setting(self.source_type), allow_redirects=False,
                )
            except requests.RequestException:
                # Do not expose signed query strings or credentials in errors.
                raise DataSource.Error("MSR v3 location request failed") from None
            if response.status_code != 200:
                raise DataSource.Error(f"MSR v3 location request failed: HTTP {response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise DataSource.Error("MSR v3 returned invalid JSON") from None
            # Explicit initial contract; adapt only against a verified MSR fixture.
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise DataSource.Error("MSR v3 expected a data array")
            if not all(isinstance(row, dict) for row in body["data"]):
                raise DataSource.Error("MSR v3 data must contain objects")
            links = body.get("links") or {}
            if not isinstance(links, dict):
                raise DataSource.Error("MSR v3 expected a links object")
            next_url = links.get("next")
            if next_url is not None and not isinstance(next_url, str):
                raise DataSource.Error("MSR v3 next link must be a URL")
            yield body["data"]
            url = urljoin(url, next_url) if next_url else None
            params = None
