from msr_etl.adapters import DataAdapter
from msr_etl.services.base import MsrETLService
from msr_etl.sinks import DataSink, IndividualImportSink, LocationImportSink
from msr_etl.source_registry import (
    DEFAULT_SOURCE_TYPE,
    resolve_individual_source,
    resolve_location_source,
)
from msr_etl.sources import DataSource
from core.models import User


class UBRIndividualService(MsrETLService):

    def __init__(self,
                 user: User,
                 source_type: str = None,
                 district: str = None,
                 ta: str = None,
                 gvh: str = None,
                 village: str = None,
                 lower_percentile_category: int = None,
                 upper_percentile_category: int = None,
                 wealth_quintiles: list = None,
                 classification: list = None,
                 gender: str = None,
                 minAge: int = None,
                 maxAge: int = None,
                 has_labour: bool = None,
                 labour_constrained: bool = None,
                 excluded_programme_codes: list = None,
                 household_head_gender: int = None,
                 source: DataSource = None,
                 adapter: DataAdapter = None,
                 sink: DataSink = None):
        if not district or not ta:
            raise ValueError("district and ta are required for UBR household imports")
        if village and not gvh:
            raise ValueError("gvh is required when village is provided")

        if lower_percentile_category is not None or upper_percentile_category is not None:
            lower = 0 if lower_percentile_category is None else lower_percentile_category
            upper = 100 if upper_percentile_category is None else upper_percentile_category
            pmt_percentile_range = range(lower, upper + 1)
        else:
            pmt_percentile_range = range(0, 11)

        source_cls, adapter_cls = resolve_individual_source(source_type)
        resolved_source_type = source_type or DEFAULT_SOURCE_TYPE

        super().__init__(
            source=source or source_cls(
                source_type=resolved_source_type,
                district=district,
                ta=ta,
                gvh=gvh,
                village=village,
                pmt_percentile_range=pmt_percentile_range,
                wealth_quintiles=wealth_quintiles,
                classification=classification,
                gender=gender,
                min_age=minAge,
                max_age=maxAge,
                has_labour=has_labour,
                labour_constrained=labour_constrained,
                excluded_programme_codes=excluded_programme_codes,
                household_head_gender=household_head_gender,
            ),
            adapter=adapter or adapter_cls(source_type=resolved_source_type),
            sink=sink or IndividualImportSink(user)
        )


class UBRLocationService(MsrETLService):

    def __init__(self,
                 user: User,
                 source_type: str = None,
                 district: str = None,
                 ta: str = None,
                 gvh: str = None,
                 village: str = None,
                 source: DataSource = None,
                 adapter: DataAdapter = None,
                 sink: DataSink = None):
        if village and not gvh:
            raise ValueError("gvh is required when village is provided")
        if gvh and not ta:
            raise ValueError("ta is required when gvh is provided")
        if ta and not district:
            raise ValueError("district is required when ta is provided")

        source_cls, adapter_cls = resolve_location_source(source_type)
        resolved_source_type = source_type or DEFAULT_SOURCE_TYPE

        super().__init__(
            source=source or source_cls(
                source_type=resolved_source_type,
                district=district,
                ta=ta,
                gvh=gvh,
                village=village,
            ),
            adapter=adapter or adapter_cls(),
            sink=sink or LocationImportSink(user)
        )
