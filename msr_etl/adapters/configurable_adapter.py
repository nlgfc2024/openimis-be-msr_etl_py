import logging

from msr_etl.adapters.base import DataAdapter

logger = logging.getLogger(__name__)


class ConfigurableAdapter(DataAdapter):
    """Generic transform driven by a target_field -> source_path map (dotted
    paths into the raw record dict, e.g. {"dob": "person.birth_date"}).

    Fits sources whose difference from the existing shape is a straight
    rename/nesting change. A source needing computed fields (lookups,
    splitting one field into two, enum translation, etc.) - the way
    UBRIndividualAdapter derives individual_role or disability - needs its
    own DataAdapter subclass instead.
    """

    def __init__(self, field_map: dict):
        if not field_map:
            raise self.Error("ConfigurableAdapter requires a non-empty field_map")
        self.field_map = field_map

    def transform(self, data):
        if data is None:
            raise self.Error("Invalid input, expected input not to be None")

        return [self._map_record(record) for record in data]

    def _map_record(self, record):
        return {
            target_field: self._resolve_path(record, source_path)
            for target_field, source_path in self.field_map.items()
        }

    @staticmethod
    def _resolve_path(record, source_path):
        value = record
        for key in source_path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value
