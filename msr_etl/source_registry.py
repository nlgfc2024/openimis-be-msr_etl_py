from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.sources import UBRIndividualSource, UBRLocationSource

DEFAULT_SOURCE_TYPE = "ubr"

INDIVIDUAL_SOURCE_REGISTRY = {
    "ubr": (UBRIndividualSource, UBRIndividualAdapter),
}

LOCATION_SOURCE_REGISTRY = {
    "ubr": (UBRLocationSource, UBRLocationAdapter),
}


class UnknownSourceType(Exception):
    pass


def resolve_individual_source(source_type=None):
    return _resolve(INDIVIDUAL_SOURCE_REGISTRY, source_type, "individual")


def resolve_location_source(source_type=None):
    return _resolve(LOCATION_SOURCE_REGISTRY, source_type, "location")


def _resolve(registry, source_type, label):
    key = source_type or DEFAULT_SOURCE_TYPE
    try:
        return registry[key]
    except KeyError:
        raise UnknownSourceType(f"Unknown {label} data source_type: '{key}'")


def list_individual_source_types():
    """source_type keys usable for individual imports: the built-in registry
    plus any admin-configured source not restricted to 'location' only."""
    return sorted(set(INDIVIDUAL_SOURCE_REGISTRY) | _configured_types_for_kind("individual"))


def list_location_source_types():
    """source_type keys usable for location imports: the built-in registry
    plus any admin-configured source not restricted to 'individual' only."""
    return sorted(set(LOCATION_SOURCE_REGISTRY) | _configured_types_for_kind("location"))


def _configured_types_for_kind(kind):
    sources = MsrEtlConfig.sources or {}
    return {
        source_type
        for source_type, config in sources.items()
        if (config or {}).get("kind", "both") in (kind, "both")
    }
