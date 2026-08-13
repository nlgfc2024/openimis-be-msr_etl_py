from msr_etl.adapters import DataAdapter, UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.services.base import MsrETLService
from msr_etl.sinks import DataSink, IndividualImportSink, LocationImportSink
from msr_etl.sources import DataSource, UBRIndividualSource, UBRLocationSource
from core.models import User


class UBRIndividualService(MsrETLService):

    def __init__(self,
                 user: User,
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

        super().__init__(
            source=source or UBRIndividualSource(
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
            adapter=adapter or UBRIndividualAdapter(),
            sink=sink or IndividualImportSink(user)
        )


class UBRLocationService(MsrETLService):

    def __init__(self,
                 user: User,
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

        super().__init__(
            source=source or UBRLocationSource(
                district=district,
                ta=ta,
                gvh=gvh,
                village=village,
            ),
            adapter=adapter or UBRLocationAdapter(),
            sink=sink or LocationImportSink(user)
        )
