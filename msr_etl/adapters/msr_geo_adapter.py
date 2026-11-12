from msr_etl.adapters.base import DataAdapter


class MsrGeoAdapter(DataAdapter):
    def __init__(self, source_type=None):
        self.source_type = source_type

    def transform(self, data):
        if (not isinstance(data, dict) or not isinstance(data.get("locations"), list)
                or not isinstance(data.get("memberships"), list) or not data.get("source_instance")):
            raise self.Error("MSR geospatial staging requires locations and verified memberships")
        # One snapshot, never the legacy code/type records expected by LocationImportSink.
        return [data]
