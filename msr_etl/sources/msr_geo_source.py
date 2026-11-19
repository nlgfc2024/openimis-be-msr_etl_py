"""Legacy source registration reusing the mapping-owned district collector."""
from mapping.providers.district import DistrictSource
from msr_etl.apps import MsrEtlConfig
from msr_etl.sources.base import StagedDataSource
from msr_etl.sources.msr_v3_geo import MsrV3GeoClient


class MsrGeoSource(DistrictSource, StagedDataSource):
    policy = "legacy"

    def __init__(self, source_type, msr_district_id=None):
        config = MsrEtlConfig.get_source_config(source_type)
        instance = config.get("source_instance")
        if not isinstance(instance, str) or not instance.strip() or len(instance) > 100:
            raise self.Error("Configure a stable source_instance for MSR geospatial imports")
        client = MsrV3GeoClient(source_type)
        super().__init__(client, instance, msr_district_id, source_type)
        client.auth.get_auth_header()
