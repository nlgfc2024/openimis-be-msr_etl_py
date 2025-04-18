from api_etl.adapters import DataAdapter, UBRAdapter, UBRLocationAdapter
from api_etl.services.base import ETLService
from api_etl.sinks import DataSink, IndividualImportSink, LocationImportSink
from api_etl.sources import DataSource, UBRSource, UBRLocationSource
from core.models import User


class UBRService(ETLService):

    def __init__(self,
                 user: User,
                 source: DataSource = None,
                 adapter: DataAdapter = None,
                 sink: DataSink = None):
        super().__init__(
            source=source or UBRSource(),
            adapter=adapter or UBRAdapter(),
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