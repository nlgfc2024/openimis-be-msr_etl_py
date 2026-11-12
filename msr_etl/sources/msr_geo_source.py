from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.sources.base import StagedDataSource
from msr_etl.sources.msr_v3_geo import MsrV3GeoClient


class MsrGeoSource(StagedDataSource):
    """One complete district per unit: ancestors and membership commit together."""

    def __init__(self, source_type, msr_district_id=None):
        self.source_type = source_type
        if (isinstance(msr_district_id, bool) or not str(msr_district_id or "").isdigit()
                or int(msr_district_id) <= 0):
            raise self.Error("A positive MSR district ID is required (not a CoreMIS ID or district code)")
        self.district_id = int(msr_district_id)
        config = MsrEtlConfig.get_source_config(source_type)
        instance = config.get("source_instance")
        if not isinstance(instance, str) or not instance.strip() or len(instance) > 100:
            raise self.Error("Configure a stable source_instance for MSR geospatial imports")
        self.source_instance = instance
        self.client = MsrV3GeoClient(source_type)
        self.client.auth.get_auth_header()  # Fail configuration validation before scheduling; no HTTP.

    def enumerate_units(self):
        return [{"unit_type": MsrEtlSyncUnit.UnitType.GEO_DISTRICT,
                 "unit_code": str(self.district_id), "district_id": self.district_id}]

    def fetch_unit(self, unit):
        district_id = unit["district_id"]
        rows = [row for page in self.client.districts() for row in page if row.get("id") == district_id]
        if len(rows) != 1:
            raise self.Error("Selected MSR district was not found uniquely")
        for kind in ("traditional_authorities", "group_village_heads", "villages", "catchments"):
            for page in self.client.locations(kind, district_id):
                for row in page:
                    if row.get("type") != kind or row.get("district_id") != district_id:
                        raise self.Error("MSR returned a location outside the requested type/district")
                    rows.append(row)
        memberships = []
        for row in rows:
            if row.get("type") == "catchments" and row.get("is_micro") is True:
                villages = []
                for page in self.client.membership(row["id"], district_id):
                    for village in page:
                        if village.get("catchment_id") != row["id"]:
                            raise self.Error("MSR membership response names a different catchment")
                        villages.append(village["id"])
                memberships.append({"catchment_id": row["id"], "revision": row["revision"], "village_ids": villages})
        # Signed access links must not enter staging, logs or persistent references.
        locations = [{k: v for k, v in row.items() if k not in ("geojson_url", "geojson_url_expires_at")} for row in rows]
        return {"source_instance": self.source_instance, "locations": locations, "memberships": memberships}

    def count_records(self, payload):
        return len(payload["locations"])

    def pull(self):
        for unit in self.enumerate_units():
            yield self.fetch_unit(unit), unit["unit_code"]
