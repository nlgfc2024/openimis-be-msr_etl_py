from api_etl.adapters import DataAdapter, UBRIndividualAdapter, UBRLocationAdapter
from api_etl.services.base import ETLService
from api_etl.sinks import DataSink, IndividualImportSink, LocationImportSink
from api_etl.sources import DataSource, UBRIndividualSource, UBRLocationSource
from core.models import User


class UBRIndividualService(ETLService):

    def __init__(self,
                 user: User,
                 source: DataSource = None,
                 adapter: DataAdapter = None,
                 sink: DataSink = None):
        super().__init__(
            source=source or UBRIndividualSource(),
            adapter=adapter or UBRIndividualAdapter(),
            sink=sink or IndividualImportSink(user)
        )


class UBRLocationService(ETLService):

    def __init__(self,
                 user: User,
                 source: DataSource = None,
                 adapter: DataAdapter = None,
                 sink: DataSink = None):
        super().__init__(
            source=source or UBRLocationSource(),
            adapter=adapter or UBRLocationAdapter(),
            sink=sink or LocationImportSink(user)
        )