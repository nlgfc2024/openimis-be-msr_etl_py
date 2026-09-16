from msr_etl.adapters import ConfigurableAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.services.base import MsrETLService
from msr_etl.sinks import IndividualImportSink, LocationImportSink
from msr_etl.sources import ConfigurableSource
from core.models import User


class ConfigurableImportService(MsrETLService):
    """Base for running the ETL pipeline against any source_type configured
    under MsrEtlConfig.sources (anything other than 'ubr', which keeps its
    own UBRIndividualService/UBRLocationService). Unlike those, this has no
    concept of district/ta/etc. - filtering is whatever the configured
    source's query_params express.
    """

    def __init__(self, user: User, source_type: str, source=None, adapter=None, sink=None):
        if not source_type:
            raise ValueError("source_type is required")

        config = MsrEtlConfig.get_source_config(source_type)
        if not config:
            raise ValueError(f"No source config found for source_type '{source_type}'")

        super().__init__(
            source=source or ConfigurableSource(config=config, source_type=source_type),
            adapter=adapter or ConfigurableAdapter(field_map=config.get("field_map") or {}),
            sink=sink or self._default_sink(user),
        )

    def _default_sink(self, user):
        raise NotImplementedError("_default_sink() not implemented")


class ConfigurableIndividualService(ConfigurableImportService):
    def _default_sink(self, user):
        return IndividualImportSink(user)


class ConfigurableLocationService(ConfigurableImportService):
    def _default_sink(self, user):
        return LocationImportSink(user)
