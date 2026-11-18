import logging

from msr_etl.apps import MsrEtlConfig
from msr_etl.location_file import DEFAULT_LOCATION_LEVELS, resolve_location_codes
from msr_etl.models import MsrEtlSyncUnit, MsrEtlUpload
from msr_etl.sources.base import StagedDataSource

logger = logging.getLogger(__name__)

# Sync runs units in this unit_type order within a unit_code, so parents exist before their children
_UNIT_TYPES = [
    MsrEtlSyncUnit.UnitType.DISTRICT,
    MsrEtlSyncUnit.UnitType.TA,
    MsrEtlSyncUnit.UnitType.GVH,
    MsrEtlSyncUnit.UnitType.VILLAGE,
]


class ExcelLocationSource(StagedDataSource):
    """
    Locations from an uploaded list (e.g. the city councils' wards and neighbourhoods). Codes are resolved
    against the MIS when the job starts, not on upload, so they reflect the MIS at import time. A unit is one
    level of one top-level location, so each city imports top-down on its own.
    """

    def __init__(self, source_type: str, upload_id=None):
        super().__init__()
        self.source_type = source_type
        if not upload_id:
            raise self.Error("upload_id is required: upload the location file first")
        upload = MsrEtlUpload.objects.filter(id=upload_id, source_type=source_type).first()
        if not upload:
            raise self.Error(f"Upload {upload_id} not found for source '{source_type}'")
        self.places = list(upload.locations or [])
        self.levels = MsrEtlConfig.get_source_config(source_type).get("location_levels") or DEFAULT_LOCATION_LEVELS
        self.level_types = [level["type"] for level in self.levels]
        if len(self.level_types) > len(_UNIT_TYPES):
            raise self.Error(f"At most {len(_UNIT_TYPES)} location levels are supported")

    def pull(self):
        for unit in self.enumerate_units():
            yield self.fetch_unit(unit), unit["unit_code"]

    def enumerate_units(self):
        resolution = resolve_location_codes(self.places, self.levels)
        for conflict in resolution["conflicts"]:
            logger.warning("Location left out of the import (row %s, %s): %s",
                           conflict["row"], conflict["value"], conflict["reason"])
        locations = resolution["locations"]
        by_key = {(location["type"], location["code"]): location for location in locations}
        units = {}
        for location in locations:
            root = location
            while root.get("parent_code"):
                parent_type = self.level_types[self.level_types.index(root["type"]) - 1]
                root = by_key[(parent_type, root["parent_code"])]
            depth = self.level_types.index(location["type"])
            unit = units.setdefault((root["code"], depth), {
                "unit_type": _UNIT_TYPES[depth],
                "unit_code": root["code"],
                "locations": [],
            })
            unit["locations"].append(location)
        return [units[key] for key in sorted(units, key=lambda key: (key[0], key[1]))]

    def fetch_unit(self, unit):
        return unit["locations"]

    def count_records(self, payload):
        return len(payload or [])

    def record_identity(self, row):
        return row["type"], row["code"]
