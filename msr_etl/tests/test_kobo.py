import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.models import AsyncJob
from core.test_helpers import LogInHelper
from individual.apps import IndividualConfig
from individual.models import Individual
from location.models import Location
from msr_etl import kobo_runs
from msr_etl.adapters import KoboIndividualAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider import get_auth_provider
from msr_etl.models import KoboSubmission, MsrEtlLocationCodeChange, MsrEtlSourceRun
from msr_etl.services.ubr_service import UBRIndividualService
from msr_etl.sinks import KoboIndividualImportSink
from msr_etl.source_registry import individual_sink_class
from msr_etl.sources import KoboSubmissionSource
from msr_etl.views import kobo_webhook

KOBO_CONFIG = {
    "connector": "kobo", "kind": "individual", "base_url": "http://kobo.test/", "asset_uid": "aForm",
    "auth_type": "token", "auth_token": "secret-token", "page_size": 2,
    "lookup_field": "json_ext__national_id", "webhook_secret": "hook-secret",
}
SOURCES = {"kobo": KOBO_CONFIG}
# The part of the UMEP individual schema these tests send
UMEP_SCHEMA = json.dumps({"$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "properties": {
    "national_id": {"type": "string", "uniqueness": True}, "sex": {"type": "string"}, "tpin": {"type": "string"},
    "kobo_uuid": {"type": "string"}, "submission_time": {"type": "string"},
}})


def _submission(kobo_id, national_id="ab12 cd34", first_name="Grace", last_name="Banda", dob="1995-04-02",
                sex="female", location_code="9010101001", **answers):
    return {
        "_id": kobo_id, "_uuid": f"uuid-{kobo_id}", "_submission_time": "2026-11-18T08:00:00",
        "formhub/uuid": "f", "meta/instanceID": f"uuid:{kobo_id}", "__version__": "v1", "_attachments": [],
        "applicant/first_name": first_name, "applicant/last_name": last_name, "applicant/dob": dob,
        "applicant/sex": sex, "applicant/national_id": national_id, "applicant/location_code": location_code,
        "applicant/location_name": "Old Name", **answers,
    }


def _response(payload, status_code=200):
    response = MagicMock(status_code=status_code, text=json.dumps(payload))
    response.json.return_value = payload
    return response


class TokenAuthTestCase(SimpleTestCase):

    @patch.object(MsrEtlConfig, "sources", SOURCES)
    def test_sends_the_kobo_token(self):
        self.assertEqual(get_auth_provider(source_type="kobo").get_auth_header(),
                         {"Authorization": "Token secret-token"})


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboSubmissionSourceTestCase(TestCase):

    def test_pages_the_submissions_after_the_last_one_stored(self):
        KoboSubmission.objects.create(source_type="kobo", asset_uid="aForm", kobo_id=7, data={})
        source = KoboSubmissionSource("kobo")
        with patch("msr_etl.sources.kobo_source.get_with_resilience", return_value=_response({"count": 3})) as get:
            units = source.enumerate_units()

        url, params = get.call_args[0][2], get.call_args[1]["params"]
        self.assertEqual(url, "http://kobo.test/api/v2/assets/aForm/data/")
        self.assertEqual(json.loads(params["query"]), {"_id": {"$gt": 7}})
        self.assertEqual(get.call_args[0][3]["Authorization"], "Token secret-token")
        self.assertEqual([(unit["after"], unit["start"]) for unit in units], [(7, 0), (7, 2)])

    def test_keeps_every_submission_it_fetches(self):
        source = KoboSubmissionSource("kobo")
        rows = [_submission(8), _submission(9)]
        with patch("msr_etl.sources.kobo_source.get_with_resilience", return_value=_response({"results": rows})) as get:
            self.assertEqual(source.fetch_unit({"after": 7, "start": 2}), rows)
            source.fetch_unit({"after": 7, "start": 2})  # a retried page stores nothing twice

        params = get.call_args[1]["params"]
        self.assertEqual((params["start"], params["limit"], json.loads(params["sort"])), (2, 2, {"_id": 1}))
        self.assertEqual(list(KoboSubmission.objects.order_by("kobo_id").values_list("kobo_id", "kobo_uuid", "status")),
                         [(8, "uuid-8", "RECEIVED"), (9, "uuid-9", "RECEIVED")])
        self.assertEqual(source.last_pulled_id(), 9)

    def test_reports_kobo_errors(self):
        source = KoboSubmissionSource("kobo")
        with patch("msr_etl.sources.kobo_source.get_with_resilience", return_value=_response({}, 401)):
            with self.assertRaisesRegex(KoboSubmissionSource.Error, "Kobo returned 401"):
                source.enumerate_units()

    def test_the_service_needs_no_district_for_kobo(self):
        service = UBRIndividualService(MagicMock(), source_type="kobo", adapter=MagicMock(), sink=MagicMock())

        self.assertIsInstance(service.source, KoboSubmissionSource)
        self.assertIs(individual_sink_class("kobo"), KoboIndividualImportSink)


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboIndividualAdapterTestCase(TestCase):

    def setUp(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        constituency = Location.objects.create(code="90101", name="Central", type="D", parent=city)
        ward = Location.objects.create(code="9010101", name="Ward A", type="W", parent=constituency)
        self.area = Location.objects.create(code="9010101001", name="Area 1", type="V", parent=ward)
        for kobo_id in (1, 2, 3, 4, 5):
            KoboSubmission.objects.create(source_type="kobo", asset_uid="aForm", kobo_id=kobo_id, data={})

    def test_flattens_the_answers_and_keeps_the_kobo_references(self):
        rows = KoboIndividualAdapter("kobo").transform([_submission(1, **{"enterprise/tpin": "T1"})])

        row = rows[0]
        self.assertEqual((row["first_name"], row["national_id"], row["tpin"]), ("Grace", "AB12CD34", "T1"))
        self.assertEqual((row["location_code"], row["location_name"]), ("9010101001", "Area 1"))
        self.assertEqual((row["kobo_id"], row["kobo_uuid"], row["submission_time"]), (1, "uuid-1", "2026-11-18T08:00:00"))
        self.assertFalse([key for key in row if key.startswith("_") or "/" in key or key == "__version__"])

    def test_follows_a_replaced_location_code(self):
        MsrEtlLocationCodeChange.objects.create(location_type="V", old_code="901T01T01T001", new_code="9010101001",
                                                location_id=self.area.id)

        rows = KoboIndividualAdapter("kobo").transform([_submission(1, location_code="901T01T01T001")])

        self.assertEqual(rows[0]["location_code"], "9010101001")

    def test_marks_what_it_cannot_import_and_gives_every_row_the_same_columns(self):
        rows = KoboIndividualAdapter("kobo").transform([
            _submission(1, **{"enterprise/tpin": "T1"}),
            _submission(2, national_id=" "),
            _submission(3, dob=""),
            _submission(4, location_code="999"),
            _submission(5, national_id="ZZ99ZZ99"),
        ])

        self.assertEqual([row["kobo_id"] for row in rows], [1, 5])
        self.assertEqual(list(rows[0]), list(rows[1]))
        self.assertEqual(rows[1]["tpin"], "")
        statuses = dict(KoboSubmission.objects.values_list("kobo_id", "status"))
        self.assertEqual((statuses[2], statuses[3], statuses[4]), ("NO_NATIONAL_ID", "INVALID", "INVALID"))
        self.assertIn("Location 999 is not in the MIS", KoboSubmission.objects.get(kobo_id=4).reason)


class KoboIndividualImportSinkTestCase(TestCase):
    """The individual import itself is replaced by one that writes the records directly."""

    def setUp(self):
        # Started here, not as class decorators: those don't cover setUp, where the sink reads its config
        for patcher in (
            patch.object(MsrEtlConfig, "sources", SOURCES),
            patch.object(MsrEtlConfig, "sink_import_username", ""),
            patch.object(IndividualConfig, "individual_schema", UMEP_SCHEMA),
            patch("msr_etl.sinks.individual_import_sink.IndividualImportService", MagicMock()),
            patch("msr_etl.sinks.individual_import_sink.WorkflowService.get_workflows",
                  lambda name, group: {"success": True, "data": {"workflows": [{"name": name}]}}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = LogInHelper().get_or_create_user_api()
        self.sink = KoboIndividualImportSink(self.user, source_type="kobo")
        self.sink._import_new_records = self._import
        self.sink._update_existing_records = self._update
        self.updated = []

    def _import(self, records, batch_identifier):
        for record in records:
            Individual(first_name=record["first_name"], last_name=record["last_name"], dob=record["dob"],
                       json_ext={k: v for k, v in record.items() if k not in ("first_name", "last_name", "dob")},
                       ).save(username=self.user.username)
        return {"data": {}}

    def _update(self, records, batch_identifier):
        self.updated.extend(records)
        for record in records:
            individual = Individual.objects.get(id=record["ID"])
            individual.json_ext = {**individual.json_ext, "tpin": record.get("tpin")}
            individual.save(username=self.user.username)
        return {"data": {}}

    def _record(self, kobo_id, **fields):
        KoboSubmission.objects.get_or_create(source_type="kobo", asset_uid="aForm", kobo_id=kobo_id, defaults={"data": {}})
        return {"kobo_id": kobo_id, "national_id": "AB12CD34", "first_name": "Grace", "last_name": "Banda",
                "dob": "1995-04-02", "sex": "female", "tpin": "", **fields}

    def _submission(self, kobo_id):
        return KoboSubmission.objects.get(kobo_id=kobo_id)

    def test_a_new_applicant_becomes_an_individual(self):
        self.sink.push([self._record(1)], "batch")

        submission = self._submission(1)
        self.assertEqual(submission.status, "IMPORTED")
        self.assertEqual(submission.individual.json_ext["national_id"], "AB12CD34")
        self.assertEqual(submission.individual.json_ext["data_source"], "kobo")

    def test_a_repeat_application_updates_the_same_record_when_the_person_matches(self):
        self.sink.push([self._record(1)], "first")

        self.sink.push([self._record(2, first_name=" GRACE ", last_name="banda", tpin="T9")], "second")

        self.assertEqual(Individual.objects.filter(json_ext__national_id="AB12CD34").count(), 1)
        self.assertEqual((self._submission(2).status, self._submission(2).individual_id),
                         ("UPDATED", self._submission(1).individual_id))
        self.assertEqual(self._submission(1).individual.json_ext["tpin"], "T9")

    def test_a_national_id_with_other_personal_details_is_held(self):
        self.sink.push([self._record(1)], "first")

        self.sink.push([self._record(2, dob="1996-04-02", sex="male")], "second")

        held = self._submission(2)
        self.assertEqual((held.status, held.individual_id), ("HELD", self._submission(1).individual_id))
        self.assertIn("dob '1995-04-02' in the MIS, '1996-04-02' applied", held.reason)
        self.assertIn("sex", held.reason)
        self.assertEqual(self.updated, [])

    def test_repeats_in_one_batch_are_compared_with_the_first_application(self):
        self.sink.push([self._record(3, tpin="T3"), self._record(1), self._record(2, last_name="Phiri")], "batch")

        self.assertEqual(Individual.objects.filter(json_ext__national_id="AB12CD34").count(), 1)
        statuses = dict(KoboSubmission.objects.values_list("kobo_id", "status"))
        self.assertEqual(statuses, {1: "IMPORTED", 2: "HELD", 3: "UPDATED"})

    def test_refuses_to_match_on_a_field_the_schema_does_not_store(self):
        schema = json.dumps({"properties": {"national_id": {"type": "string"}}})
        with patch.object(IndividualConfig, "individual_schema", schema):
            with self.assertRaisesRegex(KoboIndividualImportSink.Error, "not in the individual schema: sex"):
                KoboIndividualImportSink(self.user, source_type="kobo")

    def test_a_row_the_import_refuses_is_marked_invalid(self):
        self.sink._import_new_records = lambda records, batch: {"data": {"upload_uuid": "u1"}}
        with patch.object(KoboIndividualImportSink, "_validation_errors",
                          return_value={"AB12CD34": "location_name: not valid"}):
            self.sink.push([self._record(1)], "batch")

        self.assertEqual((self._submission(1).status, self._submission(1).reason), ("INVALID", "location_name: not valid"))


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboRunsTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()
        self.jobs = []

        def schedule(*args, **kwargs):
            job = AsyncJob.objects.create(module="msr_etl", job_type=kwargs["job_type"], task=args[0],
                                          user=self.user, params=kwargs["params"], status=AsyncJob.Status.QUEUED)
            self.jobs.append(job)
            return job.id

        patcher = patch("msr_etl.kobo_runs.run_as_scheduled_job", side_effect=schedule)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_run_at_a_time_per_source(self):
        kobo_runs.start_import("kobo", user=self.user)
        with self.assertRaises(kobo_runs.ImportAlreadyRunning):
            kobo_runs.start_import("kobo", user=self.user)

        AsyncJob.objects.filter(id=self.jobs[0].id).update(status=AsyncJob.Status.SUCCESS)
        kobo_runs.start_import("kobo", user=self.user)

        self.assertEqual(len(self.jobs), 2)
        self.assertEqual(self.jobs[1].params, {"source_type": "kobo"})
        self.assertEqual(MsrEtlSourceRun.objects.get(source_type="kobo").job_uuid, self.jobs[1].id)

    def test_a_stale_claim_does_not_block_the_source(self):
        MsrEtlSourceRun.objects.create(source_type="kobo", claimed_at=timezone.now())
        with self.assertRaises(kobo_runs.ImportAlreadyRunning):
            kobo_runs.start_import("kobo", user=self.user)

        MsrEtlSourceRun.objects.filter(source_type="kobo").update(claimed_at=timezone.now() - timedelta(minutes=10))
        kobo_runs.start_import("kobo", user=self.user)
        self.assertEqual(len(self.jobs), 1)

    def test_the_scheduler_starts_runs_as_the_import_user(self):
        with patch.object(MsrEtlConfig, "sink_import_username", self.user.username):
            kobo_runs.start_scheduled_kobo_imports()
            kobo_runs.start_scheduled_kobo_imports()  # still running: nothing new

        self.assertEqual([job.user_id for job in self.jobs], [self.user.id])

    def _hook(self, secret):
        request = APIRequestFactory().post("/api/msr_etl/kobo_webhook/kobo/", {"_id": 1}, format="json",
                                           HTTP_X_WEBHOOK_SECRET=secret)
        return kobo_webhook(request, source_type="kobo")

    def test_the_webhook_needs_the_secret(self):
        with patch.object(MsrEtlConfig, "sink_import_username", self.user.username):
            self.assertEqual(self._hook("wrong").status_code, 403)
            response = self._hook("hook-secret")
            again = self._hook("hook-secret")

        self.assertEqual((response.status_code, response.data["started"]), (202, True))
        self.assertEqual((again.status_code, again.data["started"]), (202, False))
        self.assertEqual(len(self.jobs), 1)


class KoboRealImportTestCase(TestCase):
    """The whole path through the individual module's own import and update workflows."""

    def setUp(self):
        for patcher in (patch.object(MsrEtlConfig, "sources", SOURCES),
                        patch.object(MsrEtlConfig, "sink_import_username", ""),
                        patch.object(IndividualConfig, "individual_schema", UMEP_SCHEMA),
                        patch.object(IndividualConfig, "enable_maker_checker_for_individual_upload", False),
                        patch.object(IndividualConfig, "enable_maker_checker_for_individual_update", False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = LogInHelper().get_or_create_user_api()
        city = Location.objects.create(code="901", name="Test City", type="R")
        constituency = Location.objects.create(code="90101", name="Central", type="D", parent=city)
        ward = Location.objects.create(code="9010101", name="Ward A", type="W", parent=constituency)
        self.area = Location.objects.create(code="9010101001", name="Area 1", type="V", parent=ward)

    def _run(self, submissions):
        for submission in submissions:
            KoboSubmission.objects.get_or_create(source_type="kobo", asset_uid="aForm", kobo_id=submission["_id"],
                                                 defaults={"data": submission})
        rows = KoboIndividualAdapter("kobo").transform(submissions)
        KoboIndividualImportSink(self.user, source_type="kobo").push(rows, f"test_{uuid.uuid4().hex[:8]}")

    def test_creates_then_updates_the_individual(self):
        self._run([_submission(1, **{"enterprise/tpin": "T1"})])
        self._run([_submission(2, first_name="grace", **{"enterprise/tpin": "T2", "enterprise/applicant_is_owner": "yes"})])

        individuals = Individual.objects.filter(json_ext__national_id="AB12CD34", is_deleted=False)
        self.assertEqual(individuals.count(), 1, KoboSubmission.objects.values_list("kobo_id", "status", "reason"))
        individual = individuals.get()
        self.assertEqual((individual.first_name, str(individual.dob), individual.location_id),
                         ("grace", "1995-04-02", self.area.id))
        self.assertEqual((individual.json_ext["tpin"], individual.json_ext["data_source"]), ("T2", "kobo"))
        # Not in the schema: kept on the submission, not imported
        self.assertNotIn("applicant_is_owner", individual.json_ext)
        self.assertEqual(KoboSubmission.objects.get(kobo_id=2).data["enterprise/applicant_is_owner"], "yes")
        self.assertEqual(dict(KoboSubmission.objects.values_list("kobo_id", "status")), {1: "IMPORTED", 2: "UPDATED"})
