import logging
from typing import Any, Iterable

from msr_etl.adapters.base import DataAdapter

logger = logging.getLogger(__name__)

# json_ext keys copied from the remote individual, matching UBRIndividualAdapter's output.
_INDIVIDUAL_KEYS = (
    "ubr_id", "national_id", "fit_for_work", "gender", "marital_status", "disability",
    "group_village_head_name", "group_village_head_code",
    "traditional_authority_name", "traditional_authority_code",
)
_HOUSEHOLD_KEYS = ("household_mobile_number", "household_pmt_score", "household_wealth_quintile")


class OpenimisHouseholdAdapter(DataAdapter):
    """Maps OpenimisHouseholdSource member rows to IndividualImportSink's columns."""

    def __init__(self, source_type: str = None):
        self.source_type = source_type

    def transform(self, data: Iterable[Any]) -> Iterable[Any]:
        if data is None:
            raise self.Error("Invalid input, expect input not to be None")

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
                **{key: individual_ext.get(key) for key in _INDIVIDUAL_KEYS},
                **{key: household_ext.get(key) for key in _HOUSEHOLD_KEYS},
            })
        return result
