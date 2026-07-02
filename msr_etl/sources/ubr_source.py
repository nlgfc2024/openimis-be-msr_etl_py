import logging
import os
import requests
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.auth_provider.base import AuthProvider
from msr_etl.sources import DataSource
from msr_etl.utils import get_timestamped_batch_identifier
from location.models import Location
from msr_etl.models import UBRWealthQuintiles

logger = logging.getLogger(__name__)

_DEFAULT_HOUSEHOLDS_URL = "https://malawiubr.org/api/v2/get_households_data"
_DEFAULT_GEO_LOCATIONS_URL = "https://malawiubr.org/api/v2/get_geo_locations"


def _get_int_config(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_float_config(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_bool_config(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _get_source_timeout_seconds():
    return _get_int_config(MsrEtlConfig.source_timeout_seconds, 300)


def _resolve_source_url(default_url, endpoint_path):
    configured = str(MsrEtlConfig.source_url or "").strip()
    if not configured:
        return default_url

    if configured.startswith("http://") or configured.startswith("https://"):
        if configured.endswith(endpoint_path):
            return configured
        if configured.endswith("/"):
            return f"{configured[:-1]}{endpoint_path}"
        return f"{configured}{endpoint_path}"

    logger.warning("Ignoring invalid msr_etl.source_url value: %s", configured)
    return default_url


def _get_retry_total():
    return max(_get_int_config(MsrEtlConfig.source_retry_total, 3), 0)


def _get_retry_backoff_factor():
    return max(_get_float_config(MsrEtlConfig.source_retry_backoff_factor, 1.0), 0.0)


def _get_ssl_verify_setting():
    verify_ssl = _get_bool_config(MsrEtlConfig.source_verify_ssl, True)
    ca_bundle_path = str(MsrEtlConfig.source_ca_bundle_path or "").strip()

    if not verify_ssl:
        logger.warning("msr_etl source_verify_ssl is disabled; TLS certificate verification is OFF")
        return False

    if ca_bundle_path:
        if not os.path.isfile(ca_bundle_path):
            raise DataSource.Error(
                f"source_ca_bundle_path is configured but file was not found: '{ca_bundle_path}'"
            )
        return ca_bundle_path

    return True


def _create_retry_session():
    retry_total = _get_retry_total()
    retry = Retry(
        total=retry_total,
        connect=retry_total,
        read=retry_total,
        status=retry_total,
        backoff_factor=_get_retry_backoff_factor(),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _post_with_resilience(session, url, headers, **kwargs):
    timeout_seconds = _get_source_timeout_seconds()
    verify = _get_ssl_verify_setting()
    try:
        return session.post(
            url,
            headers=headers,
            timeout=timeout_seconds,
            verify=verify,
            **kwargs,
        )
    except requests.exceptions.SSLError as exc:
        logger.exception("SSL validation failed while calling UBR endpoint %s", url)
        raise DataSource.Error(
            "SSL certificate verification failed while calling UBR API. "
            "Configure msr_etl.source_ca_bundle_path with the trusted CA chain "
            "or (only for controlled environments) set msr_etl.source_verify_ssl to false."
        ) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("HTTP request to UBR endpoint failed: %s", url)
        raise DataSource.Error(f"Failed to call UBR endpoint '{url}': {exc}") from exc


class UBRIndividualSource(DataSource):

    def __init__(
        self,
        auth_provider: AuthProvider = None,
        pmt_percentile_range: range = range(0, 11),
        district: str = None,
        ta: str = None,
        village: str = None,
        wealth_quintiles: list = None,
        classification: list = None,
        gender: str = None,
        min_age: int = None,
        max_age: int = None,
    ):
        super().__init__()

        if not (pmt_percentile_range.start >= 0 and pmt_percentile_range.stop <= 101):
            raise self.Error("pmt_percentile_range must be between 0 and 100 inclusive.")

        self.auth_provider = auth_provider or get_auth_provider()
        self.pmt_percentile_range = pmt_percentile_range
        self.district = district
        self.ta = ta
        self.village = village
        self.wealth_quintiles = wealth_quintiles or [
            UBRWealthQuintiles.POOREST.value,
            UBRWealthQuintiles.POORER.value,
            UBRWealthQuintiles.POOR.value,
        ]
        self.classification = classification
        self.gender = gender
        self.min_age = min_age
        self.max_age = max_age

    def pull(self):
        headers = {
            **MsrEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = _resolve_source_url(_DEFAULT_HOUSEHOLDS_URL, "/get_households_data")
        logger.info(f"Pulling households from {url}")

        session = _create_retry_session()

        district_codes = self._get_district_codes()

        for district_code in district_codes:
            ta_codes = self._get_ta_codes(district_code)

            for ta_code in ta_codes:
                rows = self.fetch_households(session, url, headers, district_code, ta_code)
                if rows:
                    prefix = f"batch_{district_code}_{ta_code}_"
                    identifier = get_timestamped_batch_identifier(prefix)
                    logger.info(f"Sending {len(rows)} records to data adaptor to process")
                    yield rows, identifier

                # Add a 5-second sleep after processing each TA
                logger.info(f"Sleeping for 5 seconds after processing TA: {ta_code}")
                time.sleep(5)

    def fetch(self):
        headers = {
            **MsrEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }
        url = _resolve_source_url(_DEFAULT_HOUSEHOLDS_URL, "/get_households_data")
        session = _create_retry_session()

        rows = []
        for district_code in self._get_district_codes():
            for ta_code in self._get_ta_codes(district_code):
                rows.extend(self.fetch_households(session, url, headers, district_code, ta_code))
        return rows

    def fetch_households(self, session, url, headers, district_code, ta_code):
        logger.debug(f"Fetching data for district: {district_code}, TA: {ta_code}")

        params = {
            "district_code": district_code,
            "traditional_authority_code": ta_code,
            "lower_percentile_category": str(self.pmt_percentile_range.start),
            "upper_percentile_category": str(self.pmt_percentile_range.stop - 1),
            "wealth_quintile": ",".join([str(q) for q in self.wealth_quintiles]),
        }
        if self.classification:
            params["wealth_quintile"] = ",".join([str(c) for c in self.classification])
        if self.gender:
            params["gender"] = self.gender
        if self.min_age is not None:
            params["minAge"] = str(self.min_age)
        if self.max_age is not None:
            params["maxAge"] = str(self.max_age)
        if self.village:
            self._validate_village_code(ta_code)
            params["village_code"] = self.village

        res = _post_with_resilience(
            session,
            url,
            headers,
            params=params,
        )

        if not res.ok:
            logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
            raise self.Error(f"HTTP request failed: {res.status_code}: {res.reason}")

        body = res.json()

        if body.get("error_occurred", False):
            logger.error(f"Error in response: {body.get('error_message')}")
            raise self.Error(f"Error in response: {body.get('error_message')}")

        return body.get("targeting_data", [])

    def _get_district_codes(self):
        if self.district:
            if not Location.objects.filter(
                code=self.district,
                type='D',
                validity_to__isnull=True,
            ).exists():
                raise self.Error(f"District code '{self.district}' was not found.")
            return [self.district]

        return Location.objects.filter(
            type='D',
            validity_to__isnull=True,
        ).values_list('code', flat=True)

    def _get_ta_codes(self, district_code):
        if self.ta:
            if not Location.objects.filter(
                code=self.ta,
                parent__code=district_code,
                type='W',
                validity_to__isnull=True,
            ).exists():
                raise self.Error(
                    f"TA code '{self.ta}' was not found under district '{district_code}'."
                )
            return [self.ta]

        return Location.objects.filter(
            parent__code=district_code,
            type='W',
            validity_to__isnull=True,
        ).values_list('code', flat=True)

    def _validate_village_code(self, ta_code):
        if not Location.objects.filter(
            code=self.village,
            parent__code=ta_code,
            type='V',
            validity_to__isnull=True,
        ).exists():
            raise self.Error(
                f"Village code '{self.village}' was not found under TA '{ta_code}'."
            )


class UBRLocationSource(DataSource):

    def __init__(
        self,
        auth_provider: AuthProvider = None,
    ):
        super().__init__()

        self.auth_provider = auth_provider or get_auth_provider()

    def pull(self):
        headers = {
            **MsrEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = _resolve_source_url(_DEFAULT_GEO_LOCATIONS_URL, "/get_geo_locations")
        logger.info(f"Pulling geo locations from {url}")

        session = _create_retry_session()

        district_rows = self.fetch_geo_locations_from_api(
            session, url, headers, {"geo_location_type_id": 1}, "districts"
        )

        prefix = "batch_districts_"
        identifier = get_timestamped_batch_identifier(prefix)
        yield {"data_type": "D", "data": district_rows}, identifier

        for district in district_rows:
            district_code = district.get("geo_location_code")
            if not district_code:
                logger.warning("Skipping district due to missing geo_location_code")
                continue

            logger.info(f"Fetching TAs for district: {district_code}")
            ta_rows = self.fetch_geo_locations_from_api(
                session, url, headers, {"geo_location_type_id": 2, "district_code": district_code}, "TAs"
            )

            prefix = f"batch_tas_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            yield {"data_type": "T", "data": ta_rows}, identifier

            logger.info(f"Fetching GVHs for district: {district_code}")
            gvh_rows = self.fetch_geo_locations_from_api(
                session, url, headers, {"geo_location_type_id": 4, "district_code": district_code}, "GVHs"
            )

            prefix = f"batch_gvhs_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            yield {"data_type": "G", "data": gvh_rows}, identifier

            logger.info(f"Fetching Villages for district: {district_code}")
            village_rows = self.fetch_geo_locations_from_api(
                session, url, headers, {"geo_location_type_id": 11, "district_code": district_code}, "Villages"
            )

            prefix = f"batch_villages_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            yield {"data_type": "V", "data": village_rows}, identifier

            # Add a 30-second sleep after processing each district
            logger.info(f"Sleeping for 30 seconds after processing district: {district_code}")
            time.sleep(30)

    def fetch(self, district: str = None, ta: str = None, gvh: str = None, village: str = None):
        headers = {
            **MsrEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = _resolve_source_url(_DEFAULT_GEO_LOCATIONS_URL, "/get_geo_locations")
        logger.info(f"Pulling geo locations from {url}")

        session = _create_retry_session()

        if village and not gvh:
            gvh = village[:7]
        if gvh and not ta:
            ta = gvh[:5]
        if ta and not district:
            district = ta[:3]
        if gvh and not district:
            district = gvh[:3]

        batches = []

        if not district:
            district_rows = self.fetch_geo_locations_from_api(
                session, url, headers, {"geo_location_type_id": 1}, "districts"
            )
            batches.append({"data_type": "D", "data": district_rows})
            return batches

        ta_rows = self.fetch_geo_locations_from_api(
            session, url, headers, {"geo_location_type_id": 2, "district_code": district}, "TAs"
        )
        if ta:
            ta_rows = [row for row in ta_rows if row.get("geo_location_code") == ta]
        batches.append({"data_type": "T", "data": ta_rows})

        gvh_rows = self.fetch_geo_locations_from_api(
            session, url, headers, {"geo_location_type_id": 4, "district_code": district}, "GVHs"
        )
        if ta:
            gvh_rows = [
                row for row in gvh_rows
                if row.get("parent_geo_location_code") == ta
            ]
        if gvh:
            gvh_rows = [row for row in gvh_rows if row.get("geo_location_code") == gvh]
        batches.append({"data_type": "G", "data": gvh_rows})

        village_rows = self.fetch_geo_locations_from_api(
            session, url, headers, {"geo_location_type_id": 11, "district_code": district}, "Villages"
        )
        if gvh:
            village_rows = [
                row for row in village_rows
                if row.get("parent_geo_location_code") == gvh
            ]
        if village:
            village_rows = [
                row for row in village_rows
                if row.get("geo_location_code") == village
            ]
        batches.append({"data_type": "V", "data": village_rows})
        return batches

    @staticmethod
    def fetch_geo_locations_from_api(session, url, headers, params, log_label):
        logger.info(f"Fetching {log_label} from {url} with params: {params}")

        res = _post_with_resilience(session, url, headers, json=params)

        if not res.ok:
            logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
            raise DataSource.Error(f"HTTP request failed: {res.status_code}: {res.reason}")

        body = res.json()

        if body.get("error_occurred", False):
            logger.error(f"Error in response: {body.get('error_message')}")
            raise DataSource.Error(f"Error in response: {body.get('error_message')}")

        locations = body.get("geo_locations", [])
        if not locations:
            logger.warning(f"No {log_label} found in the API response.")
            return []

        logger.info(f"Fetched {len(locations)} {log_label} from the API.")
        return locations
