from api_etl.adapters import DataAdapter, UBRAdapter
from api_etl.services.base import ETLService
from api_etl.sinks import DataSink, IndividualImportSink
from api_etl.sources import DataSource, UBRSource
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
