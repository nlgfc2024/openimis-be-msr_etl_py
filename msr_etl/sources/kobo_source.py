import json
import logging

from django.db.models import Max
from django.utils.dateparse import parse_datetime

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.models import KoboSubmission, MsrEtlSyncUnit
from msr_etl.sources.base import StagedDataSource
from msr_etl.sources.http import create_retry_session, get_int_config, get_user_agent_header, get_with_resilience

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 100
# Kobo's own cap on one page of the data API
_MAX_PAGE_SIZE = 30000


class KoboSubmissionSource(StagedDataSource):
    """
    New submissions of one Kobo form: those after the highest Kobo _id already stored. Submissions are
    never edited after they are sent, so _id is enough to know what is new. A unit is one page of them,
    counted when the job starts; later submissions wait for the next run.
    """

    def __init__(self, source_type: str, auth_provider=None):
        super().__init__()
        self.source_type = source_type
        config = self._config()
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        self.asset_uid = str(config.get("asset_uid") or "")
        if not self.base_url or not self.asset_uid:
            raise self.Error(f"Source '{source_type}' needs base_url and asset_uid")
        self.page_size = min(max(get_int_config(config.get("page_size"), _DEFAULT_PAGE_SIZE), 1), _MAX_PAGE_SIZE)
        self.auth_provider = auth_provider or get_auth_provider(source_type=source_type)

    def _config(self):
        return MsrEtlConfig.get_source_config(self.source_type)

    def last_pulled_id(self):
        return KoboSubmission.objects.filter(
            source_type=self.source_type, asset_uid=self.asset_uid,
        ).aggregate(last=Max("kobo_id"))["last"] or 0

    def _headers(self):
        return {
            "Accept": "application/json",
            **(self._config().get("headers") or {}),
            **get_user_agent_header(self.source_type),
            **self.auth_provider.get_auth_header(),
        }

    def _get(self, params, path="data/"):
        url = f"{self.base_url}/api/v2/assets/{self.asset_uid}/{path}"
        response = get_with_resilience(self.source_type, create_retry_session(self.source_type), url,
                                       self._headers(), params={"format": "json", **params})
        if response.status_code != 200:
            raise self.Error(f"Kobo returned {response.status_code} for form {self.asset_uid}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise self.Error(f"Kobo returned a response that is not JSON for form {self.asset_uid}") from exc

    def pull(self):
        for unit in self.enumerate_units():
            yield self.fetch_unit(unit), unit["unit_code"]

    def enumerate_units(self):
        after = self.last_pulled_id()
        count = int(self._get({"query": json.dumps({"_id": {"$gt": after}}), "limit": 1}).get("count") or 0)
        logger.info("Kobo form %s has %s new submissions after _id %s", self.asset_uid, count, after)
        return [
            {
                "unit_type": MsrEtlSyncUnit.UnitType.KOBO_PAGE,
                # the cursor and the page, e.g. "after 120, from 200"
                "unit_code": f"{after}+{start}",
                "after": after,
                "start": start,
            }
            for start in range(0, count, self.page_size)
        ]

    def fetch_unit(self, unit):
        rows = self._get({
            "query": json.dumps({"_id": {"$gt": unit["after"]}}),
            "sort": json.dumps({"_id": 1}),
            "start": unit["start"],
            "limit": self.page_size,
        }).get("results") or []
        self._store(rows)
        return rows

    def _store(self, rows):
        """Keep every submission pulled, whatever happens to it next."""
        KoboSubmission.objects.bulk_create([
            KoboSubmission(
                source_type=self.source_type,
                asset_uid=self.asset_uid,
                kobo_id=row["_id"],
                kobo_uuid=str(row.get("_uuid") or ""),
                submitted_at=parse_datetime(str(row.get("_submission_time") or "")),
                data=row,
            )
            for row in rows if row.get("_id") is not None
        ], ignore_conflicts=True, batch_size=500)

    def count_records(self, payload):
        return len(payload or [])

    def record_identity(self, row):
        return ("kobo", row.get("_id"))
