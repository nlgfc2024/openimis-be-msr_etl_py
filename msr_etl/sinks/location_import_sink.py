import logging
from msr_etl.sinks import DataSink
from core.models import User
from location.models import Location

logger = logging.getLogger(__name__)


class LocationImportSink(DataSink):

    def __init__(self, user: User):
        super().__init__()
        self.user = user

    def push(self, data: list[dict], batch_identifier=None):
        if not data:
            logger.debug("No location data to process.")
            return

        logger.info(f"Processing {len(data)} location records.")

        new_records, update_records = self._split_existing_and_new(data)
        self._bulk_create_locations(new_records)

        self._bulk_update_locations(update_records)

    def _split_existing_and_new(self, data: list[dict]) -> tuple:
        all_codes = [record.get("code") for record in data if record.get("code")]

        # Fetch existing locations in bulk
        existing_locations = Location.objects.filter(code__in=all_codes)
        existing_location_codes = set(existing_locations.values_list("code", flat=True))

        new_records = []
        update_records = []

        for record in data:
            location_code = record.get("code")
            if location_code in existing_location_codes:
                update_records.append(record)
            else:
                new_records.append(record)

        return new_records, update_records

    def _bulk_create_locations(self, new_records: list[dict]):
        if not new_records:
            logger.debug("No new locations to create.")
            return

        new_locations = [
            Location(
                code=record["code"],
                name=record["name"],
                type=record["type"],
                parent=record.get("parent"),
            )
            for record in new_records
        ]

        Location.objects.bulk_create(new_locations, batch_size=1000)
        logger.info(f"Bulk created {len(new_locations)} new locations.")

    def _bulk_update_locations(self, update_records: list[dict]):
        if not update_records:
            logger.debug("No existing locations to update.")
            return

        # Fetch existing locations and map them by code
        existing_locations = Location.objects.filter(
            code__in=[record["code"] for record in update_records]
        )
        existing_locations_map = {loc.code: loc for loc in existing_locations}

        for record in update_records:
            location = existing_locations_map[record["code"]]
            location.name = record["name"]
            location.type = record["type"]
            location.parent = record.get("parent")

        Location.objects.bulk_update(
            existing_locations_map.values(), ["name", "type", "parent"], batch_size=1000
        )
        logger.info(f"Bulk updated {len(update_records)} existing locations.")
