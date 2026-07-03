import abc
import logging

from msr_etl.adapters import DataAdapter
from msr_etl.sinks import DataSink
from msr_etl.sources import DataSource

logger = logging.getLogger(__name__)


class MsrETLService(metaclass=abc.ABCMeta):
    """
    ETL Service class representing a full ETL pipeline
    """

    def __init__(self,
                 source: DataSource,
                 adapter: DataAdapter,
                 sink: DataSink):
        self.source = source
        self.adapter = adapter
        self.sink = sink

    def execute(self):
        batches_processed = 0
        source_records = 0
        transformed_records = 0
        batch_identifiers = []

        try:
            for raw_batch, batch_identifier in self.source.pull():
                raw_data = raw_batch.get("data") if isinstance(raw_batch, dict) else None
                raw_count = len(raw_data) if isinstance(raw_data, list) else 0
                raw_preview = raw_data[:3] if isinstance(raw_data, list) else raw_batch
                logger.info(
                    "ETL pull batch received (id=%s, data_type=%s, count=%s)",
                    batch_identifier,
                    raw_batch.get("data_type") if isinstance(raw_batch, dict) else None,
                    raw_count,
                )
                logger.debug("ETL pull batch preview (id=%s): %s", batch_identifier, raw_preview)

                batches_processed += 1
                batch_identifiers.append(batch_identifier)
                source_records += (
                    len(raw_batch) if hasattr(raw_batch, "__len__") else 0
                )

                transformed_batch = self.adapter.transform(raw_batch)
                transformed_records += (
                    len(transformed_batch) if hasattr(transformed_batch, "__len__") else 0
                )
                transformed_count = len(transformed_batch) if isinstance(transformed_batch, list) else 0
                transformed_preview = transformed_batch[:3] if isinstance(transformed_batch, list) else transformed_batch
                logger.info(
                    "ETL transformed batch ready (id=%s, count=%s)",
                    batch_identifier,
                    transformed_count,
                )
                logger.debug("ETL transformed batch preview (id=%s): %s", batch_identifier, transformed_preview)

                self.sink.push(transformed_batch, batch_identifier)
                logger.info("ETL sink push completed (id=%s)", batch_identifier)
        except Exception as e:
            logger.error("Error in ETL pipeline: %s", str(e), exc_info=e)
            return self._error_result(str(e))

        return self._success_result({
            "batches_processed": batches_processed,
            "source_records": source_records,
            "transformed_records": transformed_records,
            "batch_identifiers": batch_identifiers,
        })

    @staticmethod
    def _error_result(detail):
        return {"success": False, "message": "Failed to execute ETL pipeline", "detail": detail}

    @staticmethod
    def _success_result(data=None):
        return {"success": True, "data": data}
