import logging
import re
from typing import Iterable

from location.models import Location

from msr_etl.adapters.base import DataAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.location_file import current_location_code
from msr_etl.models import KoboSubmission

logger = logging.getLogger(__name__)

# Kobo's own fields, not answers
_META_PREFIXES = ("_", "formhub/", "meta/")
_META_FIELDS = {"__version__", "start", "end", "today", "deviceid"}
REQUIRED_FIELDS = ("first_name", "last_name", "dob")


def normalise_national_id(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


class KoboIndividualAdapter(DataAdapter):
    """
    Kobo submissions -> one Individual row each for the individual import: answers under their field name
    without the group path (applicant/first_name -> first_name), the national ID normalised, the location
    mapped to its current MIS code and name, and the Kobo references kept. A submission that can't be
    imported is marked on its KoboSubmission and left out.
    """

    def __init__(self, source_type: str = None):
        self.source_type = source_type
        config = MsrEtlConfig.get_source_config(source_type)
        self.asset_uid = str(config.get("asset_uid") or "")
        self.national_id_field = config.get("national_id_field") or "national_id"
        self.location_field = config.get("location_field") or "location_code"
        self.location_type = config.get("location_type") or "V"

    def transform(self, data: Iterable[dict]) -> list:
        submissions = [row for row in (data or []) if row.get("_id") is not None]
        locations = self._locations(submissions)
        rows, problems = [], {}
        for submission in submissions:
            row = self._answers(submission)
            row[self.national_id_field] = normalise_national_id(row.get(self.national_id_field))
            problem = self._problem(row, locations)
            if problem:
                problems[submission["_id"]] = problem
                continue
            location = locations[row[self.location_field]]
            row["location_code"], row["location_name"] = location.code, location.name
            row.update(kobo_id=submission["_id"], kobo_uuid=str(submission.get("_uuid") or ""),
                       submission_time=str(submission.get("_submission_time") or ""))
            rows.append(row)
        self._mark(problems)
        return self._same_columns(rows)

    @staticmethod
    def _answers(submission):
        answers = {}
        for key, value in submission.items():
            if key.startswith(_META_PREFIXES) or key in _META_FIELDS:
                continue
            answers[key.split("/")[-1]] = "" if value is None else value
        return answers

    def _locations(self, submissions):
        """{code in the answer: current MIS location}, following replaced temporary codes."""
        codes = {str(self._answers(s).get(self.location_field) or "") for s in submissions} - {""}
        current = {code: current_location_code(self.location_type, code) for code in codes}
        found = {loc.code: loc for loc in Location.objects.filter(
            type=self.location_type, code__in=set(current.values()), validity_to__isnull=True)}
        return {code: found[new] for code, new in current.items() if new in found}

    def _problem(self, row, locations):
        if not row.get(self.national_id_field):
            return KoboSubmission.Status.NO_NATIONAL_ID, "No national ID"
        missing = [field for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            return KoboSubmission.Status.INVALID, f"Missing {', '.join(missing)}"
        code = str(row.get(self.location_field) or "")
        if code not in locations:
            return KoboSubmission.Status.INVALID, f"Location {code or '(none)'} is not in the MIS"
        return None

    def _mark(self, problems):
        for kobo_id, (status, reason) in problems.items():
            KoboSubmission.objects.filter(source_type=self.source_type, asset_uid=self.asset_uid,
                                          kobo_id=kobo_id).update(status=status, reason=reason)
            logger.info("Kobo submission %s not imported: %s", kobo_id, reason)

    @staticmethod
    def _same_columns(rows):
        # The import file takes its columns from the first row, so every row needs them all
        columns = list(dict.fromkeys(key for row in rows for key in row))
        return [{column: row.get(column, "") for column in columns} for row in rows]
