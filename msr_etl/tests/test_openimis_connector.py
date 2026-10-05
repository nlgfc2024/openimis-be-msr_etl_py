import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from msr_etl.adapters import OpenimisHouseholdAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.source_registry import (
    list_individual_source_types,
    list_location_source_types,
    resolve_individual_source,
)
from msr_etl.sources import OpenimisHouseholdSource

PWP_CONFIG = {
    "connector": "openimis_gql",
    "kind": "individual",
    "base_url": "https://pwp.example.org/api/graphql",
    "auth_type": "openimis_jwt",
    "user_agent": "msr-etl-connector",
    "household_filters": ['validation_status__exact__string="VERIFIED"'],
    "member_filters": {"validation_status": "VERIFIED"},
    "page_size": 2,
}


def _member(ubr_id, role="SON", recipient=None, status="VERIFIED"):
    return {"node": {
        "role": role, "recipientType": recipient, "jsonExt": {},
        "individual": {
            "uuid": f"ind-{ubr_id}", "firstName": f"First{ubr_id}", "lastName": "Last", "dob": "1990-01-01",
            "jsonExt": json.dumps({"ubr_id": ubr_id, "validation_status": status, "gender": "Female", "national_id": f"N{ubr_id}"}),
            "location": {"code": "105010104", "name": "Chalunda2"},
        },
    }}


def _household(code, members):
    return {"node": {
        "code": code,
        "jsonExt": json.dumps({"form_number": f"F{code}", "household_wealth_quintile": "Poorest", "validation_status": "VERIFIED"}),
        "location": {"code": "105010104", "name": "Chalunda2"},
        "groupindividuals": {"edges": members},
    }}


def _response(data=None, errors=None, status_code=200):
    body = {"data": data}
    if errors:
        body["errors"] = [{"message": message} for message in errors]
    return MagicMock(ok=status_code < 400, status_code=status_code, json=MagicMock(return_value=body))


LOCATION_FOUND = _response({"locations": {"edges": [{"node": {"uuid": "remote-ta-uuid"}}]}})


def _page(households, has_next, cursor=None):
    return _response({"group": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "edges": households}})


class OpenimisConnectorTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)
        MsrEtlConfig.sources = {"pwp": {**PWP_CONFIG}}
        self.auth = MagicMock()
        self.auth.get_auth_header.return_value = {"Authorization": "Bearer jwt"}
        patcher = patch("msr_etl.sources.openimis_source.post_with_resilience")
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        session_patcher = patch("msr_etl.sources.openimis_source.create_retry_session", return_value=MagicMock())
        session_patcher.start()
        self.addCleanup(session_patcher.stop)

    def _source(self, **scope):
        return OpenimisHouseholdSource(source_type="pwp", auth_provider=self.auth, **{"district": "105", **scope})

    def _ta_unit(self, code="10501"):
        return {"unit_type": MsrEtlSyncUnit.UnitType.TA, "unit_code": code, "location_code": code, "location_type": "D"}

    def test_requires_district(self):
        with self.assertRaises(OpenimisHouseholdSource.Error):
            OpenimisHouseholdSource(source_type="pwp", auth_provider=self.auth)

    @patch("msr_etl.sources.openimis_source.Location")
    def test_enumerates_one_ta_unit_per_district_ta(self, mock_location):
        mock_location.objects.filter.return_value.order_by.return_value.values_list.return_value = ["10501", "10502"]

        units = self._source().enumerate_units()

        self.assertEqual([u["unit_code"] for u in units], ["10501", "10502"])
        self.assertTrue(all(u["unit_type"] == MsrEtlSyncUnit.UnitType.TA and u["location_type"] == "D" for u in units))
        mock_location.objects.filter.assert_called_once_with(
            parent__code="105", parent__type="R", type="D", validity_to__isnull=True,
        )

    def test_most_specific_scope_is_a_single_unit(self):
        self.assertEqual(self._source(ta="10501").enumerate_units(), [self._ta_unit()])
        gvh_unit = self._source(ta="10501", gvh="1050101").enumerate_units()
        self.assertEqual([(u["unit_type"], u["location_type"]) for u in gvh_unit], [(MsrEtlSyncUnit.UnitType.GVH, "W")])
        village_unit = self._source(ta="10501", gvh="1050101", village="105010104").enumerate_units()
        self.assertEqual([(u["unit_type"], u["location_type"]) for u in village_unit], [(MsrEtlSyncUnit.UnitType.VILLAGE, "V")])

    def test_fetch_unit_pages_households_and_flattens_members(self):
        self.post.side_effect = [
            LOCATION_FOUND,
            _page([_household("H1", [_member(1, "HEAD"), _member(2)]), _household("H2", [_member(3, None, "PRIMARY")])], True, "c1"),
            _page([_household("H3", [_member(4)])], False),
        ]

        rows = self._source().fetch_unit(self._ta_unit())

        self.assertEqual([r["individual"]["jsonExt"]["ubr_id"] for r in rows], [1, 2, 3, 4])
        self.assertEqual(rows[0]["household"]["jsonExt"]["form_number"], "FH1")
        location_call, first_page, second_page = self.post.call_args_list
        self.assertEqual(location_call.kwargs["json"]["variables"], {"code": "10501", "type": "D"})
        variables = first_page.kwargs["json"]["variables"]
        self.assertEqual(variables["location"], "remote-ta-uuid")
        self.assertEqual(variables["level"], 1)
        self.assertEqual(variables["first"], 2)
        self.assertEqual(variables["filters"], PWP_CONFIG["household_filters"])
        self.assertIsNone(variables["after"])
        self.assertEqual(second_page.kwargs["json"]["variables"]["after"], "c1")
        headers = first_page.args[3]
        self.assertEqual(headers["User-Agent"], "msr-etl-connector")
        self.assertEqual(headers["Authorization"], "Bearer jwt")

    def test_member_filters_drop_unverified_members(self):
        self.post.side_effect = [
            LOCATION_FOUND,
            _page([_household("H1", [_member(1, "HEAD"), _member(2, status="NOT_VERIFIED")])], False),
        ]

        rows = self._source().fetch_unit(self._ta_unit())

        self.assertEqual([r["individual"]["jsonExt"]["ubr_id"] for r in rows], [1])

    def test_empty_member_filters_keep_every_member(self):
        MsrEtlConfig.sources["pwp"]["member_filters"] = {}
        self.post.side_effect = [
            LOCATION_FOUND,
            _page([_household("H1", [_member(1, "HEAD"), _member(2, status="NOT_VERIFIED")])], False),
        ]

        self.assertEqual(len(self._source().fetch_unit(self._ta_unit())), 2)

    def test_page_size_is_capped_at_the_relay_limit(self):
        MsrEtlConfig.sources["pwp"]["page_size"] = 500
        self.post.side_effect = [LOCATION_FOUND, _page([], False)]

        self._source().fetch_unit(self._ta_unit())

        self.assertEqual(self.post.call_args_list[1].kwargs["json"]["variables"]["first"], 100)

    def test_expired_token_logs_in_again_once(self):
        self.post.side_effect = [
            LOCATION_FOUND,
            _response(errors=["Signature has expired"]),
            _page([_household("H1", [_member(1, "HEAD")])], False),
        ]

        rows = self._source().fetch_unit(self._ta_unit())

        self.assertEqual(len(rows), 1)
        self.auth.invalidate.assert_called_once()

    def test_http_401_logs_in_again_once(self):
        self.post.side_effect = [LOCATION_FOUND, _response(status_code=401), _page([], False)]

        self._source().fetch_unit(self._ta_unit())

        self.auth.invalidate.assert_called_once()

    def test_repeated_auth_failure_raises(self):
        self.post.side_effect = [LOCATION_FOUND, _response(status_code=401), _response(status_code=401)]

        with self.assertRaisesRegex(OpenimisHouseholdSource.Error, "HTTP 401"):
            self._source().fetch_unit(self._ta_unit())

    def test_graphql_errors_raise(self):
        self.post.side_effect = [LOCATION_FOUND, _response(errors=["Cannot query field \"x\""])]

        with self.assertRaisesRegex(OpenimisHouseholdSource.Error, "GraphQL errors"):
            self._source().fetch_unit(self._ta_unit())

    def test_csrf_error_explains_the_bypass(self):
        self.post.side_effect = [_response(errors=["'csrftoken'"])]

        with self.assertRaisesRegex(OpenimisHouseholdSource.Error, "USER_AGENT_CSRF_BYPASS"):
            self._source().fetch_unit(self._ta_unit())

    def test_unknown_remote_location_raises(self):
        self.post.side_effect = [_response({"locations": {"edges": []}})]

        with self.assertRaisesRegex(OpenimisHouseholdSource.Error, "not found on 'pwp'.*assign it the TAs"):
            self._source().fetch_unit(self._ta_unit())

    def test_missing_base_url_raises(self):
        MsrEtlConfig.sources["pwp"]["base_url"] = ""

        with self.assertRaisesRegex(OpenimisHouseholdSource.Error, "requires base_url"):
            self._source().fetch_unit(self._ta_unit())

    def test_record_identity_and_count(self):
        source = self._source()
        rows = [{"individual": {"jsonExt": {"ubr_id": 7}}}, {"individual": {"jsonExt": {}}}]
        self.assertEqual(source.record_identity(rows[0]), ("ubr_id", "7"))
        self.assertIsNone(source.record_identity(rows[1]))
        self.assertEqual(source.count_records(rows), 2)


class OpenimisHouseholdAdapterTestCase(SimpleTestCase):

    def _row(self, ubr_id=1, role="son", recipient=None):
        return {
            "household": {"code": "H1", "jsonExt": {"form_number": "F1", "household_wealth_quintile": "Poorest"},
                          "location": {"code": "105010104", "name": "Chalunda2"}},
            "membership": {"role": role, "recipientType": recipient, "jsonExt": {}},
            "individual": {"uuid": "ind-1", "firstName": "Ann", "lastName": "Banda", "dob": "1990-01-01",
                           "jsonExt": {"ubr_id": ubr_id, "national_id": "N1", "gender": "Female"},
                           "location": {"code": "105010104", "name": "Chalunda2"}},
        }

    def test_maps_member_rows_to_sink_columns(self):
        (row,) = OpenimisHouseholdAdapter("pwp").transform([self._row()])

        self.assertEqual(row["group_code"], "H1")
        self.assertEqual(row["form_number"], "F1")
        self.assertEqual((row["first_name"], row["last_name"], row["dob"]), ("Ann", "Banda", "1990-01-01"))
        self.assertEqual((row["location_code"], row["location_name"]), ("105010104", "Chalunda2"))
        self.assertEqual(row["individual_role"], "SON")
        self.assertEqual((row["ubr_id"], row["national_id"], row["gender"]), (1, "N1", "Female"))
        self.assertEqual(row["household_wealth_quintile"], "Poorest")

    def test_primary_recipient_without_role_gets_empty_role(self):
        (row,) = OpenimisHouseholdAdapter("pwp").transform([self._row(role=None, recipient="PRIMARY")])
        self.assertEqual(row["individual_role"], "")

    def test_skips_members_without_ubr_id(self):
        self.assertEqual(OpenimisHouseholdAdapter("pwp").transform([self._row(ubr_id=None)]), [])

    def test_rejects_none(self):
        with self.assertRaises(OpenimisHouseholdAdapter.Error):
            OpenimisHouseholdAdapter("pwp").transform(None)


class OpenimisRegistryAndServiceTestCase(SimpleTestCase):

    def setUp(self):
        self._original_sources = MsrEtlConfig.sources
        self.addCleanup(setattr, MsrEtlConfig, "sources", self._original_sources)
        MsrEtlConfig.sources = {"pwp": {**PWP_CONFIG}}

    def test_registered_for_individual_imports_only(self):
        self.assertEqual(resolve_individual_source("pwp"), (OpenimisHouseholdSource, OpenimisHouseholdAdapter))
        self.assertIn("pwp", list_individual_source_types())
        self.assertNotIn("pwp", list_location_source_types())

    @patch("msr_etl.sources.openimis_source.get_auth_provider")
    def test_service_builds_the_source_without_ubr_only_arguments(self, mock_auth):
        from msr_etl.services import UBRIndividualService

        service = UBRIndividualService(MagicMock(), source_type="pwp", district="105", ta="10501", sink=MagicMock())

        self.assertIsInstance(service.source, OpenimisHouseholdSource)
        self.assertEqual((service.source.district, service.source.ta), ("105", "10501"))
        self.assertIsInstance(service.adapter, OpenimisHouseholdAdapter)
        mock_auth.assert_called_once_with(source_type="pwp")
