from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.sources import UBRIndividualSource, UBRLocationSource

DEFAULT_SOURCE_TYPE = "ubr"
DEFAULT_CONNECTOR = "msr_api"

# connector -> (DataSource, DataAdapter). A source_type's config picks one
# with its "connector" key; "ubr" falls back to msr_api when unset.
INDIVIDUAL_CONNECTOR_REGISTRY = {
    "msr_api": (UBRIndividualSource, UBRIndividualAdapter),
}

LOCATION_CONNECTOR_REGISTRY = {
    "msr_api": (UBRLocationSource, UBRLocationAdapter),
}


class UnknownSourceType(Exception):
    pass


def resolve_individual_source(source_type=None):
    return _resolve(INDIVIDUAL_CONNECTOR_REGISTRY, source_type, "individual")


def resolve_location_source(source_type=None):
    return _resolve(LOCATION_CONNECTOR_REGISTRY, source_type, "location")


def get_connector(source_type=None):
    key = source_type or DEFAULT_SOURCE_TYPE
    connector = MsrEtlConfig.get_source_config(key).get("connector")
    if not connector and key == DEFAULT_SOURCE_TYPE:
        return DEFAULT_CONNECTOR
    return connector


def _resolve(registry, source_type, label):
    key = source_type or DEFAULT_SOURCE_TYPE
    if not _supports_kind(key, label):
        raise UnknownSourceType(f"Source type '{key}' is not configured for {label} imports")
    try:
        return registry[get_connector(key)]
    except KeyError:
        raise UnknownSourceType(f"Unknown {label} data source_type: '{key}'")


def list_individual_source_types():
    """source_types whose connector supports individual imports."""
    return _list_for_kind(INDIVIDUAL_CONNECTOR_REGISTRY, "individual")


def list_location_source_types():
    """source_types whose connector supports location imports."""
    return _list_for_kind(LOCATION_CONNECTOR_REGISTRY, "location")


def _list_for_kind(registry, kind):
    source_types = set(MsrEtlConfig.sources or {}) | {DEFAULT_SOURCE_TYPE}
    return sorted(
        source_type
        for source_type in source_types
        if get_connector(source_type) in registry and _supports_kind(source_type, kind)
    )


def _supports_kind(source_type, kind):
    return MsrEtlConfig.get_source_config(source_type).get("kind", "both") in (kind, "both")
