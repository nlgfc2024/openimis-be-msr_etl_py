import logging
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from msr_etl.apps import MsrEtlConfig
from msr_etl.sources.base import DataSource

logger = logging.getLogger(__name__)


def get_int_config(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float_config(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_bool_config(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return default


def get_timeout_seconds(source_type):
    config = MsrEtlConfig.get_source_config(source_type)
    return get_int_config(config.get("timeout_seconds"), 300)


def get_retry_total(source_type):
    config = MsrEtlConfig.get_source_config(source_type)
    return max(get_int_config(config.get("retry_total"), 3), 0)


def get_retry_backoff_factor(source_type):
    config = MsrEtlConfig.get_source_config(source_type)
    return max(get_float_config(config.get("retry_backoff_factor"), 1.0), 0.0)


def get_ssl_verify_setting(source_type):
    config = MsrEtlConfig.get_source_config(source_type)
    verify_ssl = get_bool_config(config.get("verify_ssl"), True)
    ca_bundle_path = str(config.get("ca_bundle_path") or "").strip()

    if not verify_ssl:
        logger.warning("verify_ssl is disabled for msr_etl source '%s'; TLS certificate verification is OFF", source_type)
        return False

    if ca_bundle_path:
        if not os.path.isfile(ca_bundle_path):
            raise DataSource.Error(
                f"ca_bundle_path is configured for source '{source_type}' but file was not found: '{ca_bundle_path}'"
            )
        return ca_bundle_path

    return True


def get_user_agent_header(source_type):
    """User-Agent the remote may need, e.g. to match openIMIS USER_AGENT_CSRF_BYPASS."""
    user_agent = str(MsrEtlConfig.get_source_config(source_type).get("user_agent") or "").strip()
    return {"User-Agent": user_agent} if user_agent else {}


def resolve_source_url(source_type, default_url, endpoint_path):
    config = MsrEtlConfig.get_source_config(source_type)
    configured = str(config.get("base_url") or "").strip()
    if not configured:
        return default_url

    if configured.startswith("http://") or configured.startswith("https://"):
        if configured.endswith(endpoint_path):
            return configured
        if configured.endswith("/"):
            return f"{configured[:-1]}{endpoint_path}"
        return f"{configured}{endpoint_path}"

    logger.warning("Ignoring invalid base_url for msr_etl source '%s': %s", source_type, configured)
    return default_url


def create_retry_session(source_type):
    retry_total = get_retry_total(source_type)
    retry = Retry(
        total=retry_total,
        connect=retry_total,
        read=retry_total,
        status=retry_total,
        backoff_factor=get_retry_backoff_factor(source_type),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def post_with_resilience(source_type, session, url, headers, **kwargs):
    try:
        return session.post(
            url,
            headers=headers,
            timeout=get_timeout_seconds(source_type),
            verify=get_ssl_verify_setting(source_type),
            **kwargs,
        )
    except requests.exceptions.SSLError as exc:
        logger.exception("SSL validation failed while calling '%s' endpoint %s", source_type, url)
        raise DataSource.Error(
            f"SSL certificate verification failed while calling the '{source_type}' endpoint. "
            "Configure its ca_bundle_path with the trusted CA chain "
            "or (only for controlled environments) set verify_ssl to false."
        ) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("HTTP request to '%s' endpoint failed: %s", source_type, url)
        raise DataSource.Error(f"Failed to call '{source_type}' endpoint '{url}': {exc}") from exc
