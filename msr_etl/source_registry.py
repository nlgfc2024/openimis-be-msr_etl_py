from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
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
