import logging
import time

from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSyncUnit, MsrEtlUpload
from msr_etl.sources.http import create_retry_session, get_int_config
from msr_etl.sources.ubr_source import UBRIndividualSource, _resolve_households_url

logger = logging.getLogger(__name__)

_DEFAULT_FORM_NUMBERS_PER_UNIT = 50


class MsrFormNumberSource(UBRIndividualSource):
    """
    Whole households listed by form_number in an uploaded file (e.g. UPG's SCTP export), each fetched
    from MSR's get_households_data. MSR answers one household per request, and only with the
    percentile range set, so a unit is a batch of form numbers fetched one by one.
    """

    def __init__(self, source_type: str, upload_id=None, auth_provider=None):
        super().__init__(auth_provider=auth_provider, source_type=source_type)
        if not upload_id:
            raise self.Error("upload_id is required: upload the file of form numbers first")
        upload = MsrEtlUpload.objects.filter(id=upload_id, source_type=source_type).first()
        if not upload:
            raise self.Error(f"Upload {upload_id} not found for source '{source_type}'")
        self.form_numbers = list(upload.form_numbers or [])

    def pull(self):
        for unit in self.enumerate_units():
            yield self.fetch_unit(unit), unit["unit_code"]

    def enumerate_units(self):
        size = max(get_int_config(self._config().get("form_numbers_per_unit"), _DEFAULT_FORM_NUMBERS_PER_UNIT), 1)
        return [
            {
                "unit_type": MsrEtlSyncUnit.UnitType.FORM_NUMBERS,
                # positions in the uploaded list, e.g. "51-100"
                "unit_code": f"{start + 1}-{start + len(chunk)}",
                "form_numbers": chunk,
            }
            for start in range(0, len(self.form_numbers), size)
            for chunk in [self.form_numbers[start:start + size]]
        ]

    def fetch_unit(self, unit):
        headers = self._get_headers()
        url = _resolve_households_url(self.source_type)
        session = create_retry_session(self.source_type)
        delay = float(self._config().get("form_number_delay_seconds") or 0)

        rows, missing = [], []
        for position, form_number in enumerate(unit["form_numbers"]):
            if position and delay > 0:
                time.sleep(delay)
            found = [
                row for row in self._post_households(session, url, headers, {
                    "form_number": form_number,
                    "lower_percentile_category": "0",
                    "upper_percentile_category": "100",
                })
                if str(row.get("form_number")) == form_number
            ]
            if not found:
                missing.append(form_number)
            rows.extend(found)
        if missing:
            logger.warning("Form numbers not found in MSR for source '%s': %s", self.source_type, missing)
        return rows

    def _config(self):
        return MsrEtlConfig.get_source_config(self.source_type)
