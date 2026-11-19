"""Compatibility facade; the verified transport now belongs to mapping."""
from mapping.providers.msr_v3 import MsrV3Provider
from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.bearerAuthProvider import BearerAuthProvider
from msr_etl.sources.base import DataSource
from msr_etl.sources.http import create_retry_session


class MsrV3GeoClient(MsrV3Provider):
    def __init__(self, source_type="msr_geo_v3", session=None):
        self.source_type = source_type
        super().__init__(MsrEtlConfig.get_source_config(source_type),
                         session=session if session is not None else create_retry_session(source_type),
                         error_type=DataSource.Error)
        self.auth = BearerAuthProvider(source_type=source_type)
