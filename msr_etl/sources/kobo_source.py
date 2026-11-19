import json
import logging

from django.utils.dateparse import parse_datetime

from msr_etl.adapters.kobo_adapter import normalise_national_id
from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.kobo_form import normalise_form, store_form
from msr_etl.models import KoboFormVersion, KoboSubmission, MsrEtlSyncUnit
from msr_etl.sources.base import StagedDataSource
from msr_etl.sources.http import (
    create_retry_session, get_int_config, get_user_agent_header, get_with_resilience, send_with_resilience,
)

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 100
# Kobo's own cap on one page of the data API
_MAX_PAGE_SIZE = 30000


class KoboSubmissionSource(StagedDataSource):
    """
    The submissions of one Kobo form that the MIS hasn't finished: those Kobo has but the MIS hasn't stored, and
    stored ones still RECEIVED (an earlier run failed on them). Submissions aren't edited after they are sent,
    so their _id is enough to know which are new. Nothing is skipped for good: a page that fails is fetched again
    by the next run. A unit is up to page_size submissions; later ones wait for the next run.
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

    def _stored(self):
        return KoboSubmission.objects.filter(source_type=self.source_type, asset_uid=self.asset_uid)

    def kobo_ids(self):
        """Every submission id the form has, oldest first (ids only, so one request covers many)."""
        ids, start = [], 0
        while True:
            results = self._get({"fields": json.dumps(["_id"]), "sort": json.dumps({"_id": 1}),
                                 "start": start, "limit": _MAX_PAGE_SIZE}).get("results") or []
            ids.extend(row["_id"] for row in results if row.get("_id") is not None)
            if len(results) < _MAX_PAGE_SIZE:
                return ids
            start += len(results)

    def _headers(self):
        return {
            "Accept": "application/json",
            **(self._config().get("headers") or {}),
            **get_user_agent_header(self.source_type),
            **self.auth_provider.get_auth_header(),
        }

    def request(self, method, path, expected=(200,), **kwargs):
        """A call to this form's API (e.g. "DELETE", "files/<uid>/"); anything but an expected status is an error."""
        url = f"{self.base_url}/api/v2/assets/{self.asset_uid}/{path}"
        send = getattr(create_retry_session(self.source_type), method.lower())
        response = send_with_resilience(self.source_type, send, url, self._headers(), **kwargs)
        if response.status_code not in expected:
            raise self.Error(f"Kobo returned {response.status_code} for {method} {path or 'the form'}: "
                             f"{response.text[:300]}")
        return response

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

    def sync_form(self):
        """Keep the deployed form version, if it is new, so its submissions can be shown as the form."""
        version_uid = self._get({}, path="").get("deployed_version_id")
        if not version_uid:
            return None
        stored = KoboFormVersion.objects.filter(source_type=self.source_type, asset_uid=self.asset_uid,
                                                version_uid=version_uid).first()
        if stored:
            return stored
        content = self._get({}, path=f"versions/{version_uid}/").get("content") or {}
        form = normalise_form(content.get("survey") or [], content.get("choices") or [],
                              content.get("settings") or {}, version_uid=version_uid)
        logger.info("Stored version %s of Kobo form %s", form["version"] or version_uid, self.asset_uid)
        return store_form(self.source_type, self.asset_uid, form)

    def enumerate_units(self):
        try:
            self.sync_form()
        except Exception:
            # The view needs it, the import doesn't
            logger.exception("Could not store the form of Kobo form %s", self.asset_uid)
        stored = set(self._stored().values_list("kobo_id", flat=True))
        missing = [kobo_id for kobo_id in self.kobo_ids() if kobo_id not in stored]
        # Only one run per source at a time, so a RECEIVED row here is one an earlier run didn't finish
        unfinished = list(self._stored().filter(status=KoboSubmission.Status.RECEIVED)
                          .order_by("kobo_id").values_list("kobo_id", flat=True))
        logger.info("Kobo form %s: %s new submissions, %s to retry", self.asset_uid, len(missing), len(unfinished))
        return self._units(missing, "new", stored=False) + self._units(unfinished, "retry", stored=True)

    def _units(self, ids, label, stored):
        return [
            {
                "unit_type": MsrEtlSyncUnit.UnitType.KOBO_PAGE,
                # e.g. "new 201-300", "retry 12-12"
                "unit_code": f"{label} {chunk[0]}-{chunk[-1]}",
                "kobo_ids": chunk,
                "stored": stored,
            }
            for start in range(0, len(ids), self.page_size)
            for chunk in [ids[start:start + self.page_size]]
        ]

    def fetch_unit(self, unit):
        if unit["stored"]:
            return [submission.data for submission in self._stored().filter(kobo_id__in=unit["kobo_ids"])
                    .order_by("kobo_id")]
        rows = self._get({
            "query": json.dumps({"_id": {"$in": unit["kobo_ids"]}}),
            "sort": json.dumps({"_id": 1}),
            "limit": len(unit["kobo_ids"]),
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
                national_id=self._national_id(row),
                data=row,
            )
            for row in rows if row.get("_id") is not None
        ], ignore_conflicts=True, batch_size=500)

    def _national_id(self, row):
        field = self._config().get("national_id_field") or "national_id"
        answer = next((value for key, value in row.items() if key.split("/")[-1] == field), "")
        return normalise_national_id(answer)[:64]

    def count_records(self, payload):
        return len(payload or [])

    def record_identity(self, row):
        return ("kobo", row.get("_id"))
