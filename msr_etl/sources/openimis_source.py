import json
import logging

from location.models import Location

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.auth_provider.base import AuthProvider
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.sources.base import StagedDataSource
from msr_etl.sources.http import (
    create_retry_session,
    get_int_config,
    get_user_agent_header,
    post_with_resilience,
)

logger = logging.getLogger(__name__)

# openIMIS location types, top-down; parentLocationLevel is the index.
_LOCATION_LEVELS = {"R": 0, "D": 1, "W": 2, "V": 3}
_UNIT_TYPES = {
    "D": MsrEtlSyncUnit.UnitType.TA,
    "W": MsrEtlSyncUnit.UnitType.GVH,
    "V": MsrEtlSyncUnit.UnitType.VILLAGE,
}
_MAX_PAGE_SIZE = 100
_AUTH_ERROR_MARKERS = ("signature has expired", "error decoding signature", "unauthorized", "permission denied")

LOCATION_UUID_QUERY = """
query($code: String!, $type: String!) {
  locations(code: $code, type: $type) { edges { node { uuid } } }
}
"""

HOUSEHOLDS_QUERY = """
query($first: Int!, $after: String, $location: String!, $level: Int!, $filters: [String]) {
  group(first: $first, after: $after, parentLocation: $location, parentLocationLevel: $level, customFilters: $filters) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      code jsonExt location { code name }
      groupindividuals { edges { node {
        role recipientType jsonExt
        individual { uuid firstName lastName dob jsonExt location { code name } }
      } } }
    } }
  }
}
"""


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except ValueError:
            return {}
    return value or {}


class OpenimisHouseholdSource(StagedDataSource):
    """
    Pulls households and their members from a remote openIMIS instance (base_url is
    its /api/graphql), one location unit at a time. Units come from the local
    Location table; both instances share MSR location codes but not UUIDs.
    """

    def __init__(
        self,
        source_type: str,
        district: str = None,
        ta: str = None,
        gvh: str = None,
        village: str = None,
        auth_provider: AuthProvider = None,
    ):
        super().__init__()
        if not district:
            raise self.Error("district is required")
        self.source_type = source_type
        self.district = district
        self.ta = ta
        self.gvh = gvh
        self.village = village
        self.auth_provider = auth_provider or get_auth_provider(source_type=source_type)
        self._session = None

    def pull(self):
        for unit in self.enumerate_units():
            yield self.fetch_unit(unit), unit["unit_code"]

    def enumerate_units(self):
        """One unit per TA under the district, or the single most specific location given."""
        for code, location_type in ((self.village, "V"), (self.gvh, "W"), (self.ta, "D")):
            if code:
                return [self._unit(code, location_type)]

        ta_codes = Location.objects.filter(
            parent__code=self.district, parent__type="R", type="D", validity_to__isnull=True,
        ).order_by("code").values_list("code", flat=True)
        return [self._unit(code, "D") for code in ta_codes]

    def fetch_unit(self, unit):
        location_uuid = self._remote_location_uuid(unit["location_code"], unit["location_type"])
        if not location_uuid:
            raise self.Error(
                f"Location {unit['location_type']} {unit['location_code']} not found on '{self.source_type}', "
                "or not visible to its service user (assign it the TAs it may read)"
            )

        rows = []
        after = None
        while True:
            page = self._graphql(HOUSEHOLDS_QUERY, {
                "first": self._page_size(),
                "after": after,
                "location": location_uuid,
                "level": _LOCATION_LEVELS[unit["location_type"]],
                "filters": self._config().get("household_filters") or [],
            })["group"]
            for edge in page["edges"]:
                rows.extend(self._member_rows(edge["node"]))
            if not page["pageInfo"]["hasNextPage"]:
                return rows
            after = page["pageInfo"]["endCursor"]

    def count_records(self, payload):
        return len(payload)

    def record_identity(self, row):
        ubr_id = (row.get("individual") or {}).get("jsonExt", {}).get("ubr_id")
        return ("ubr_id", str(ubr_id)) if ubr_id not in (None, "") else None

    def _unit(self, code, location_type):
        return {
            "unit_type": _UNIT_TYPES[location_type],
            "unit_code": code,
            "location_code": code,
            "location_type": location_type,
        }

    def _member_rows(self, household):
        household = {**household, "jsonExt": _json(household.get("jsonExt"))}
        memberships = household.pop("groupindividuals", None) or {}
        member_filters = self._config().get("member_filters") or {}
        rows = []
        for edge in memberships.get("edges") or []:
            membership = edge["node"]
            individual = {**(membership.get("individual") or {})}
            individual["jsonExt"] = _json(individual.get("jsonExt"))
            if any(individual["jsonExt"].get(key) != value for key, value in member_filters.items()):
                continue
            rows.append({
                "household": household,
                "membership": {
                    "role": membership.get("role"),
                    "recipientType": membership.get("recipientType"),
                    "jsonExt": _json(membership.get("jsonExt")),
                },
                "individual": individual,
            })
        return rows

    def _remote_location_uuid(self, code, location_type):
        edges = self._graphql(LOCATION_UUID_QUERY, {"code": code, "type": location_type})["locations"]["edges"]
        return edges[0]["node"]["uuid"] if edges else None

    def _graphql(self, query, variables, retry_auth=True):
        url = str(self._config().get("base_url") or "").strip()
        if not url:
            raise self.Error(f"Source '{self.source_type}' requires base_url (the remote /api/graphql)")
        if self._session is None:
            self._session = create_retry_session(self.source_type)
        headers = {
            "Content-Type": "application/json",
            **(self._config().get("headers") or {}),
            **get_user_agent_header(self.source_type),
            **self.auth_provider.get_auth_header(),
        }
        response = post_with_resilience(
            self.source_type, self._session, url, headers,
            json={"query": query, "variables": variables},
        )

        if response.status_code == 401 or (response.ok and self._has_auth_error(response.json())):
            if retry_auth and hasattr(self.auth_provider, "invalidate"):
                self.auth_provider.invalidate()
                return self._graphql(query, variables, retry_auth=False)
        if not response.ok:
            raise self.Error(f"'{self.source_type}' GraphQL request failed: HTTP {response.status_code}")

        body = response.json()
        if body.get("errors"):
            messages = "; ".join(str(e.get("message")) for e in body["errors"])
            if "csrftoken" in messages:
                messages += " (the remote must list this source's user_agent in USER_AGENT_CSRF_BYPASS)"
            raise self.Error(f"'{self.source_type}' GraphQL errors: {messages}")
        return body["data"]

    @staticmethod
    def _has_auth_error(body):
        messages = " ".join(str(e.get("message", "")) for e in body.get("errors") or []).lower()
        return any(marker in messages for marker in _AUTH_ERROR_MARKERS)

    def _page_size(self):
        return min(max(get_int_config(self._config().get("page_size"), _MAX_PAGE_SIZE), 1), _MAX_PAGE_SIZE)

    def _config(self):
        return MsrEtlConfig.get_source_config(self.source_type)
