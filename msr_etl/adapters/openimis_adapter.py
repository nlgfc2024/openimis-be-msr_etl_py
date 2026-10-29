import json
import logging
from typing import Any, Iterable

from individual.data_source import DATA_SOURCE_FIELD
from msr_etl.adapters.base import DataAdapter

logger = logging.getLogger(__name__)

_INDIVIDUAL_KEYS = (
    "ubr_id", "national_id", "fit_for_work", "gender", "marital_status", "disability",
    "group_village_head_name", "group_village_head_code",
    "traditional_authority_name", "traditional_authority_code",
)
_HOUSEHOLD_KEYS = ("household_mobile_number", "household_pmt_score", "household_wealth_quintile")
_RESERVED_KEYS = {
    "id", "ID", "group_code", "form_number", "first_name", "last_name", "dob", "location_name",
    "location_code", "individual_role", "recipient_info", DATA_SOURCE_FIELD,
}
_RECIPIENT_INFO = {"PRIMARY": 1, "SECONDARY": 2}


class OpenimisHouseholdAdapter(DataAdapter):
    """Maps OpenimisHouseholdSource member rows to IndividualImportSink's columns."""

    def __init__(self, source_type: str = None):
        self.source_type = source_type

    def transform(self, data: Iterable[Any]) -> Iterable[Any]:
        if data is None:
            raise self.Error("Invalid input, expect input not to be None")

        data = list(data)
        extra_keys = self._extra_individual_keys(data)
        result = []
        for row in data:
            individual = row.get("individual") or {}
            individual_ext = individual.get("jsonExt") or {}
            household = row.get("household") or {}
            household_ext = household.get("jsonExt") or {}
            membership = row.get("membership") or {}

            if individual_ext.get("ubr_id") in (None, ""):
                logger.warning("Skipping member %s with no ubr_id", individual.get("uuid"))
                continue

            location = individual.get("location") or household.get("location") or {}
            group_code = household.get("code")
            result.append({
                "group_code": group_code,
                "form_number": household_ext.get("form_number") or group_code,
                "first_name": individual.get("firstName"),
                "last_name": individual.get("lastName"),
                "dob": individual.get("dob"),
                "location_name": location.get("name"),
                "location_code": location.get("code"),
                # Primary recipients who aren't the head have no role on the remote membership.
                "individual_role": (membership.get("role") or "").upper(),
                "recipient_info": _RECIPIENT_INFO.get(membership.get("recipientType")),
                **{key: individual_ext.get(key) for key in _INDIVIDUAL_KEYS},
                **{key: individual_ext.get(key) for key in extra_keys},
                **{key: household_ext.get(key) for key in _HOUSEHOLD_KEYS},
            })
        return result

    @staticmethod
    def _extra_individual_keys(data):
        """Remote individual fields beyond the fixed ones that the local individual_schema declares, so
        new remote fields come through once declared there (the import rejects undeclared columns).
        Collected over the batch: every row needs the same columns for the import file."""
        remote_keys = {
            key for row in data for key in ((row.get("individual") or {}).get("jsonExt") or {})
            if key not in _RESERVED_KEYS and key not in _INDIVIDUAL_KEYS and key not in _HOUSEHOLD_KEYS
        }
        declared = _declared_individual_fields()
        skipped = sorted(remote_keys - declared)
        if skipped:
            logger.info("Not importing remote individual fields missing from individual_schema: %s", skipped)
        return sorted(remote_keys & declared)


def _declared_individual_fields():
    from individual.apps import IndividualConfig

    schema = IndividualConfig.individual_schema or {}
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            schema = {}
    return set((schema.get("properties") or {}).keys())
