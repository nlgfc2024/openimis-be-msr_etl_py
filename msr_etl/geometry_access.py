"""Refresh MSR file access server-side; never send service tokens to the browser."""
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

import requests

from msr_etl.apps import MsrEtlConfig
from msr_etl.sources.msr_v3_geo import MsrV3GeoClient
from msr_etl.sources.http import get_ssl_verify_setting, get_timeout_seconds

MAX_BYTES = 5 * 1024 * 1024
from location.geography import safe_geojson



def download_geometry(reference):
    result = {"status": "delivery_unavailable", "revision": str(reference.revision),
              "relation": reference.geometry_relation, "source": reference.geometry_source, "geojson": None}
    options = []
    for key in (MsrEtlConfig.sources or {}):
        config = MsrEtlConfig.get_source_config(key)
        if (config.get("enabled", True) and config.get("connector") == "msr_geo_v3"
                and config.get("source_instance") == reference.source_instance):
            options.append(key)
    if len(options) != 1:
        return {**result, "status": "source_unconfigured"}
    source_type = options[0]
    try:
        client = MsrV3GeoClient(source_type)
        # Retry once with newly issued access if a file link expires in transit.
        for attempt in range(2):
            with client.session.get(
                f"{client.url}/{reference.msr_type}/{reference.msr_id}",
                headers=client.auth.get_auth_header(), timeout=get_timeout_seconds(source_type),
                verify=get_ssl_verify_setting(source_type), allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    return result
                row = response.json().get("data")
            if (not isinstance(row, dict) or row.get("id") != reference.msr_id
                    or row.get("type") != reference.msr_type or type(row.get("revision")) is not int
                    or row["revision"] < reference.revision):
                return result
            result.update(revision=str(row["revision"]), relation=row.get("geometry_relation"), source=row.get("source"))
            if row.get("boundary_status") == "unavailable":
                return {**result, "status": "unavailable"}
            if row.get("boundary_status") != "available":
                return result
            signed = urlsplit(row.get("geojson_url") or "")
            origin = urlsplit(client.url)
            if ((signed.scheme, signed.netloc) != (origin.scheme, origin.netloc)
                    or not re.fullmatch(r"/geo-files/[0-9]+", signed.path)
                    or signed.username or signed.password or signed.fragment):
                return result
            with client.session.get(
                row["geojson_url"], headers={"Accept": "application/geo+json"},
                timeout=get_timeout_seconds(source_type), verify=get_ssl_verify_setting(source_type),
                allow_redirects=False, stream=True,
            ) as response:
                if response.status_code in (401, 403) and attempt == 0:
                    continue
                if response.status_code != 200:
                    return result
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return {**result, "status": "geometry_too_large"}
                    chunks.append(chunk)
            raw = b"".join(chunks)
            expected_hash = row.get("geometry_sha256")
            if not expected_hash or hashlib.sha256(raw).hexdigest() != expected_hash:
                return {**result, "status": "integrity_failure"}
            return {**result, "status": "available", "geojson": safe_geojson(json.loads(raw)), "sha256": expected_hash}
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return result
    except Exception:
        # Auth/configuration errors must not expose tokens or signed URLs via GraphQL.
        return result
    return result



def load_geometry(reference):
    """Deprecated local display alias; MSR transport uses download_geometry."""
    from location.geography import legacy_geometry
    return legacy_geometry(reference)
