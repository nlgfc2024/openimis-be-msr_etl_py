import logging
from msr_etl.sinks import DataSink
from core.models import User
from location.models import Location
from django.db.models import Q

logger = logging.getLogger(__name__)


class LocationImportSink(DataSink):

    def __init__(self, user: User):
        super().__init__()
        self.user = user

    def push(self, data: list[dict], batch_identifier=None):
        if not data:
            return

        normalized_data = [record for record in data if record.get("code") and record.get("type")]
        if not normalized_data:
            logger.warning("No valid location records to process (missing code/type).")
            return

        # Keep only the latest row per (code, type) key in the current batch.
        deduped_by_key = {}
        for record in normalized_data:
            deduped_by_key[(record["code"], record["type"])] = record
        normalized_data = list(deduped_by_key.values())

        logger.info(f"Processing {len(normalized_data)} location records.")

        new_records, update_records = self._split_existing_and_new(normalized_data)
        logger.info(
            "Location sink split summary (batch_id=%s, new=%s, update=%s)",
            batch_identifier,
            len(new_records),
            len(update_records),
        )

        self._bulk_create_locations(new_records)

        self._bulk_update_locations(update_records)

    def _split_existing_and_new(self, data: list[dict]) -> tuple:
        all_codes = list({record.get("code") for record in data if record.get("code")})
        all_types = list({record.get("type") for record in data if record.get("type")})

        # Fetch only active locations relevant to incoming records.
        existing_locations = Location.objects.filter(
            code__in=all_codes,
            type__in=all_types,
            validity_to__isnull=True,
        )
        existing_location_keys = {(loc.code, loc.type) for loc in existing_locations}

        new_records = []
        update_records = []

        for record in data:
            location_code = record.get("code")
            location_type = record.get("type")
            if (location_code, location_type) in existing_location_keys:
                update_records.append(record)
            else:
                new_records.append(record)

        return new_records, update_records

    def _bulk_create_locations(self, new_records: list[dict]):
        if not new_records:
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
            return

        query = Q()
        for record in update_records:
            query |= Q(
                code=record["code"],
                type=record["type"],
                validity_to__isnull=True,
            )

        # Fetch existing active locations and map them by (code, type)
        existing_locations = Location.objects.filter(query)
        existing_locations_map = {(loc.code, loc.type): loc for loc in existing_locations}
        updated_locations = []

        for record in update_records:
            key = (record["code"], record["type"])
            location = existing_locations_map.get(key)
            if not location:
                logger.warning(
                    "Skipping location update for missing active record code=%s type=%s",
                    record["code"],
                    record["type"],
                )
                continue
            location.name = record["name"]
            location.type = record["type"]
            location.parent = record.get("parent")
            updated_locations.append(location)

        if not updated_locations:
            return

        Location.objects.bulk_update(
            updated_locations, ["name", "type", "parent"], batch_size=1000
        )
        logger.info(f"Bulk updated {len(updated_locations)} existing locations.")
