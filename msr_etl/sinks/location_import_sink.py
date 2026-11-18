import logging
from msr_etl.sinks.base import DataSink
from core.models import User
from location.models import Location
from django.db.models import Q

from msr_etl.location_file import TEMPORARY_CODE_KEY
from msr_etl.models import MsrEtlLocationCodeChange

logger = logging.getLogger(__name__)


class LocationImportSink(DataSink):

    def __init__(self, user: User, source_type: str = None):
        # Locations are shared reference data (MSR codes on every instance), so not scoped by source
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

        normalized_data = self._replace_codes(normalized_data, batch_identifier)
        new_records, update_records = self._split_existing_and_new(normalized_data)
        logger.info(
            "Location sink split summary (batch_id=%s, new=%s, update=%s)",
            batch_identifier,
            len(new_records),
            len(update_records),
        )

        self._bulk_create_locations(new_records)

        self._bulk_update_locations(update_records)

    def _replace_codes(self, data: list[dict], batch_identifier) -> list[dict]:
        """Records with a previous_code give that location its new code (a temporary code replaced); the
        location keeps its id, so whatever points at it follows. Returns the records still to import."""
        replacing = [record for record in data if record.get("previous_code")]
        if not replacing:
            return data
        query = Q()
        for record in replacing:
            query |= Q(code=record["previous_code"], type=record["type"]) | Q(code=record["code"], type=record["type"])
        active = {(loc.code, loc.type): loc for loc in Location.objects.filter(query, validity_to__isnull=True)}

        replaced, changes, done = [], [], set()
        for record in replacing:
            location = active.get((record["previous_code"], record["type"]))
            # Already replaced (e.g. a retried unit), or the new code is taken: import it by its code as usual
            if location is None or (record["code"], record["type"]) in active:
                continue
            location.code = record["code"]
            self._apply(location, record)
            replaced.append(location)
            changes.append(MsrEtlLocationCodeChange(
                location_type=record["type"], old_code=record["previous_code"], new_code=record["code"],
                location_id=location.id, batch_identifier=batch_identifier or "",
            ))
            done.add(id(record))
        if replaced:
            Location.objects.bulk_update(replaced, ["code", "name", "type", "parent", "json_ext"], batch_size=1000)
            MsrEtlLocationCodeChange.objects.bulk_create(changes, batch_size=1000)
            logger.info("Replaced %s location codes (batch_id=%s)", len(replaced), batch_identifier)
        return [record for record in data if id(record) not in done]

    @staticmethod
    def _apply(location, record):
        location.name = record["name"]
        location.type = record["type"]
        location.parent = record.get("parent")
        # Only sources that track temporary codes send the flag
        if "temporary" in record:
            json_ext = dict(location.json_ext or {})
            if record["temporary"]:
                json_ext[TEMPORARY_CODE_KEY] = True
            else:
                json_ext.pop(TEMPORARY_CODE_KEY, None)
            location.json_ext = json_ext

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

        new_locations = []
        for record in new_records:
            location = Location(code=record["code"])
            self._apply(location, record)
            new_locations.append(location)

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
            self._apply(location, record)
            updated_locations.append(location)

        if not updated_locations:
            return

        fields = ["name", "type", "parent"]
        if any("temporary" in record for record in update_records):
            fields.append("json_ext")
        Location.objects.bulk_update(updated_locations, fields, batch_size=1000)
        logger.info(f"Bulk updated {len(updated_locations)} existing locations.")
