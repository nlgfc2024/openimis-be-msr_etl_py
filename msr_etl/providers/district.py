"""District source implementing CoreMIS's existing staged-source protocol."""
from msr_etl.providers.msr_v3 import ProviderError

LOCATION_FIELDS = {"id", "type", "code", "name", "district_id", "parents", "latitude", "longitude",
                   "point_status", "is_micro", "geometry_relation", "geometry_sha256", "source",
                   "revision", "geojson_updated_at", "boundary_status"}


class DistrictSource:
    policy = "msr_etl"

    def __init__(self, provider, source_instance, district_id, source_type="msr_geo_v3"):
        provider._id(district_id)
        self.client = provider
        self.source_instance = source_instance
        self.district_id = int(district_id)
        self.source_type = source_type

    def enumerate_units(self):
        return [{"unit_type": "GEO_DISTRICT", "unit_code": str(self.district_id), "district_id": self.district_id}]

    def fetch_unit(self, unit):
        district_id = unit["district_id"]
        rows = [row for page in self.client.districts() for row in page if row.get("id") == district_id]
        if len(rows) != 1:
            raise ProviderError("MSR_GEO_DISTRICT_NOT_UNIQUE")
        for kind in ("traditional_authorities", "group_village_heads", "villages", "catchments"):
            for page in self.client.locations(kind, district_id):
                for row in page:
                    if row.get("type") != kind or row.get("district_id") != district_id:
                        raise ProviderError("MSR returned a location outside the requested type/district")
                    rows.append(row)
        identities = [(row.get("type"), row.get("id")) for row in rows]
        if len(identities) != len(set(identities)):
            raise ProviderError("MSR_GEO_DUPLICATE_SOURCE_IDENTITY")
        memberships, assigned = [], set()
        for row in rows:
            if row.get("type") == "catchments" and row.get("is_micro") is True:
                villages = []
                for page in self.client.membership(row["id"], district_id):
                    for village in page:
                        if village.get("catchment_id") != row["id"]:
                            raise ProviderError("MSR_GEO_MEMBERSHIP_CATCHMENT_MISMATCH")
                        self.client._id(village.get("id"))
                        if village["id"] in assigned:
                            raise ProviderError("MSR_GEO_MEMBERSHIP_CONFLICT")
                        assigned.add(village["id"])
                        villages.append(village["id"])
                memberships.append({"catchment_id": row["id"], "revision": row["revision"], "village_ids": villages})
        locations = [{key: value for key, value in row.items() if key in LOCATION_FIELDS} for row in rows]
        from types import SimpleNamespace
        geometries = []
        for row in locations:
            if row.get("boundary_status") != "available":
                continue
            from msr_etl.geometry_access import download_geometry
            reference = SimpleNamespace(source_instance=self.source_instance, msr_type=row['type'],
                msr_id=row['id'], revision=row['revision'], geometry_relation=row.get('geometry_relation'),
                geometry_source=row.get('source'))
            geometries.append({'type': row['type'], 'id': row['id'], 'result': download_geometry(reference)})
        return {"source_instance": self.source_instance, "locations": locations, "memberships": memberships, "geometries": geometries}

    def count_records(self, payload):
        return len(payload["locations"])

    def pull(self):
        for unit in self.enumerate_units():
            yield self.fetch_unit(unit), unit["unit_code"]
