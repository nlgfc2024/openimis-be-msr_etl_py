"""MSR wire adapter extracted from the verified CoreMIS transport.

No ORM, host model or startup work. Tokens and request URLs never enter errors.
"""
from copy import deepcopy
import os
from urllib.parse import urlsplit, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TYPES = {"districts", "traditional_authorities", "group_village_heads", "villages",
         "catchments", "constituencies", "wards", "epas", "sections", "clusters", "zones"}


class ProviderError(ValueError):
    pass


class Bearer:
    def __init__(self, token, error_type):
        self.token, self.error_type = token, error_type

    def get_auth_header(self):
        if not self.token:
            raise self.error_type("MSR_GEO_SOURCE_CREDENTIAL_MISSING")
        return {"Authorization": "Bearer " + self.token}


class MsrV3Provider:
    def __init__(self, config, session=None, error_type=ProviderError):
        self.config = deepcopy(config)
        self.Error = error_type
        base = str(config.get("base_url") or "").strip()
        parsed = urlsplit(base)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise self.Error("MSR_GEO_INVALID_BASE_URL")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise self.Error("MSR_GEO_HTTPS_REQUIRED")
        self.base = base.rstrip("/")
        self.url = self._endpoint(config.get("list_path", "/api/v3/geo-locations"))
        self.timeout = self._integer("timeout_seconds", 60, 1, 300)
        self.max_pages = self._integer("max_pages", 1000, 1, 10000)
        self.max_records = self._integer("max_records", 100000, 1, 1000000)
        verify = config.get("verify_ssl", True)
        self.verify = config.get("ca_bundle_path") or verify
        if verify is False:
            raise self.Error("MSR_GEO_TLS_VERIFICATION_REQUIRED")
        token = os.environ.get(config["token_env"], "") if config.get("token_env") else config.get("auth_bearer_token", "")
        self.auth = Bearer(token, self.Error)
        self.session = session if session is not None else self._session()

    def _integer(self, name, default, minimum, maximum):
        try:
            value = int(self.config.get(name, default))
        except (TypeError, ValueError):
            raise self.Error("MSR_GEO_INVALID_REQUEST_LIMIT") from None
        if not minimum <= value <= maximum:
            raise self.Error("MSR_GEO_INVALID_REQUEST_LIMIT")
        return value

    def _session(self):
        retries = self._integer("retry_total", 3, 0, 5)
        retry = Retry(total=retries, connect=retries, read=retries, status=retries,
                      backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET"]), raise_on_status=False)
        session = requests.Session()
        for scheme in ("https://", "http://"):
            session.mount(scheme, HTTPAdapter(max_retries=retry))
        return session

    def _endpoint(self, path):
        if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
            raise self.Error("MSR_GEO_INVALID_ENDPOINT")
        endpoint = self.base + path
        if urlsplit(endpoint).netloc != urlsplit(self.base).netloc:
            raise self.Error("MSR_GEO_INVALID_ENDPOINT")
        return endpoint

    def _get(self, url, params=None):
        try:
            response = self.session.get(url, params=params,
                headers={"Accept": "application/json", **self.auth.get_auth_header()},
                timeout=self.timeout, verify=self.verify, allow_redirects=False)
        except requests.RequestException:
            raise self.Error("MSR_GEO_SOURCE_REQUEST_FAILED") from None
        if response.status_code != 200:
            raise self.Error(f"MSR_GEO_SOURCE_HTTP_{response.status_code}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise self.Error("MSR_GEO_SOURCE_INVALID_JSON") from None
        if not isinstance(body, dict):
            raise self.Error("MSR_GEO_SOURCE_INVALID_ENVELOPE")
        return body

    def districts(self):
        return self._pages({"type": "districts"})

    def locations(self, location_type, district_id):
        if location_type not in TYPES:
            raise self.Error("MSR_GEO_UNKNOWN_LOCATION_TYPE")
        self._id(district_id)
        return self._pages({"type": location_type, "district_id": str(district_id)})

    def catchments(self, district_id):
        return self.locations("catchments", district_id)

    def get_location(self, location_type, location_id):
        if location_type not in TYPES:
            raise self.Error("MSR_GEO_UNKNOWN_LOCATION_TYPE")
        self._id(location_id)
        path = self.config.get("detail_path", "/api/v3/geo-locations/{type}/{id}")
        row = self._get(self._endpoint(path.format(type=location_type, id=int(location_id)))).get("data")
        if not isinstance(row, dict) or row.get("id") != int(location_id) or row.get("type") != location_type:
            raise self.Error("MSR_GEO_SOURCE_INVALID_IDENTITY")
        return row

    def membership(self, catchment_id, district_id):
        self._id(catchment_id)
        self._id(district_id)
        path = self.config.get("membership_path", "/api/v2/micro-catchments/{id}/villages")
        return self._pages({"district_id": str(district_id), "per_page": 100},
                           endpoint=self._endpoint(path.format(id=int(catchment_id))), legacy=True)

    def _id(self, value):
        if isinstance(value, bool) or not str(value or "").isdigit() or int(value) <= 0:
            raise self.Error("MSR_GEO_INVALID_SOURCE_ID")

    def _pages(self, params, endpoint=None, legacy=False):
        endpoint = endpoint or self.url
        expected = urlsplit(endpoint)
        url, visited, count = endpoint, set(), 0
        while url:
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path) or parsed.fragment:
                raise self.Error("MSR_GEO_PAGINATION_ORIGIN_REJECTED")
            if url in visited:
                raise self.Error("MSR pagination cycle detected")
            if len(visited) >= self.max_pages:
                raise self.Error("MSR_GEO_PAGE_LIMIT_EXCEEDED")
            visited.add(url)
            body = self._get(url, params)
            rows = body.get("data")
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise self.Error("MSR v3 expected a data array of objects")
            count += len(rows)
            if count > self.max_records:
                raise self.Error("MSR_GEO_RECORD_LIMIT_EXCEEDED")
            links = body.get("links") or {}
            if not legacy and not isinstance(links, dict):
                raise self.Error("MSR v3 expected a links object")
            next_url = body.get("next_page_url") if legacy else links.get("next")
            if next_url is not None and not isinstance(next_url, str):
                raise self.Error("MSR_GEO_INVALID_NEXT_PAGE")
            yield rows
            url, params = (urljoin(url, next_url) if next_url else None), None
