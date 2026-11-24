import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from core.test_helpers import LogInHelper
from individual.models import Individual
from location.models import Location
from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_form import (
    application_sections, form_fields, form_for_submission, individual_schema, normalise_form, raw_answers,
    read_xlsform, store_form,
)
from msr_etl.models import KoboFormVersion, KoboSubmission
from msr_etl.schema import Query
from msr_etl.sources import KoboSubmissionSource
from msr_etl.tests.test_kobo import SOURCES, _response

FORM_FILE = os.path.join(os.path.dirname(__file__), "fixtures", "umep_application_form.xlsx")
# The shape of Kobo's API content for the same kind of questions
KOBO_CONTENT = {
    "settings": {"version": "2026111827", "id_string": "urban_mep_application"},
    "survey": [
        {"name": "start", "type": "start"},
        {"name": "applicant", "type": "begin_group", "label": ["Step 3 of 17 · Applicant information"]},
        {"name": "first_name", "type": "text", "label": ["First Name (as on National ID)"]},
        {"name": "legal_form", "type": "select_one", "label": ["Legal Form"], "select_from_list_name": "legal_form"},
        {"name": "note1", "type": "note", "label": ["Read this"]},
        {"type": "end_group"},
        {"name": "jobs", "type": "integer", "label": ["Jobs"]},
    ],
    "choices": [{"list_name": "legal_form", "name": "partnership_group", "label": ["Partnership/Group"]}],
}


class NormaliseFormTestCase(SimpleTestCase):

    def test_reads_the_final_xlsform(self):
        form = read_xlsform(FORM_FILE)

        self.assertEqual((form["id_string"], form["version"]), ("urban_mep_application", "2026112402"))
        # Steps 1 and 2 hold only notes
        self.assertEqual(form["sections"][0]["label"], "Step 3 of 17 · Applicant information")
        self.assertEqual(len(form["sections"]), 15)
        fields = {field["name"]: field for field in form_fields(form)}
        self.assertEqual((fields["legal_form"]["type"], fields["legal_form"]["list"]), ("select_one", "legal_form"))
        self.assertEqual((fields["res_ward"]["type"], fields["res_ward"]["file"]), ("select_one_from_file", "wards.csv"))
        self.assertNotIn("program_summary", fields)
        # Asked only when Bank Statements is ticked
        self.assertEqual((fields["business_bank"]["type"], fields["business_bank"]["list"]), ("select_one", "bank"))
        self.assertEqual(form["choices"]["bank"]["other"], "Other")
        # For Annex 2B: months in operation and a stable market
        self.assertEqual(fields["business_start_date"]["type"], "date")
        self.assertEqual(sorted(form["choices"]["market_stability"]), ["irregular", "stable"])
        self.assertEqual(fields["msr_form_number"]["type"], "text")
        self.assertNotIn("start", fields)
        self.assertEqual(form["choices"]["contribution_source"]["business_assets"], "Business Assets")

    def test_reads_kobo_api_content_the_same_way(self):
        form = normalise_form(KOBO_CONTENT["survey"], KOBO_CONTENT["choices"], KOBO_CONTENT["settings"], "vUid")

        self.assertEqual([s["label"] for s in form["sections"]], ["Step 3 of 17 · Applicant information", ""])
        self.assertEqual([f["name"] for f in form_fields(form)], ["first_name", "legal_form", "jobs"])
        self.assertEqual(form["choices"], {"legal_form": {"partnership_group": "Partnership/Group"}})
        self.assertEqual(form["version_uid"], "vUid")

    def test_the_schema_covers_every_answer_but_the_base_fields(self):
        schema = individual_schema(read_xlsform(FORM_FILE))

        properties = schema["properties"]
        self.assertEqual(len(properties), 102)
        for bank_answer in ("business_account_name", "business_bank", "business_bank_other", "business_account_number"):
            self.assertEqual(properties[bank_answer]["type"], "string")
        for native in ("first_name", "last_name", "dob", "location_code", "location_name"):
            self.assertNotIn(native, properties)
        self.assertEqual(properties["national_id"], {"type": "string", "title": "National ID Number", "uniqueness": True})
        self.assertEqual(properties["years_in_operation"]["type"], "integer")
        self.assertEqual(properties["grant_amount_usd"]["type"], "number")
        self.assertEqual(properties["contribution_sources"]["type"], "string")
        self.assertIn("kobo_uuid", properties)


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboFormStoreTestCase(TestCase):

    def test_the_source_keeps_the_deployed_version_once(self):
        source = KoboSubmissionSource("kobo")
        replies = [_response({"deployed_version_id": "vUid"}), _response({"content": KOBO_CONTENT}),
                   _response({"deployed_version_id": "vUid"})]
        with patch("msr_etl.sources.kobo_source.get_with_resilience", side_effect=replies) as get:
            source.sync_form()
            source.sync_form()

        self.assertEqual(get.call_args_list[1][0][2], "http://kobo.test/api/v2/assets/aForm/versions/vUid/")
        self.assertEqual(list(KoboFormVersion.objects.values_list("version_uid", "version")), [("vUid", "2026111827")])

    def test_a_failing_form_fetch_does_not_stop_the_import(self):
        source = KoboSubmissionSource("kobo")
        with patch("msr_etl.sources.kobo_source.get_with_resilience",
                   side_effect=[_response({}, 500), _response({"count": 0})]):
            self.assertEqual(source.enumerate_units(), [])

    def test_the_command_stores_an_xlsform_and_writes_the_individual_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "umep.individual.config.json")
            with open(path, "w") as handle:
                json.dump({"enable_python_workflows": True}, handle)
            call_command("kobo_form", source="kobo", xlsform=FORM_FILE, individual_config=path, stdout=io.StringIO())
            config = json.load(open(path))

        self.assertTrue(config["enable_python_workflows"])
        self.assertEqual(len(json.loads(config["individual_schema"])["properties"]), 102)
        self.assertEqual(KoboFormVersion.objects.get().version, "2026112402")

    def test_a_submission_is_shown_with_its_own_form_version(self):
        old = store_form("kobo", "aForm", {**normalise_form(KOBO_CONTENT["survey"], [], {"version": "1"}, "vOld")})
        store_form("kobo", "aForm", read_xlsform(FORM_FILE))
        submission = KoboSubmission(source_type="kobo", asset_uid="aForm", kobo_id=1, data={"__version__": "vOld"})

        self.assertEqual(form_for_submission(submission), old.form)
        # Another version's labels could be wrong for it, so no form rather than the latest
        submission.data = {"__version__": "unknown"}
        self.assertIsNone(form_for_submission(submission))

    def test_without_its_form_version_a_submission_shows_its_raw_answers(self):
        submission = KoboSubmission(source_type="kobo", asset_uid="aForm", kobo_id=1, data={
            "__version__": "unknown", "_id": 1, "meta/instanceID": "uuid:1", "formhub/uuid": "f",
            "applicant/first_name": "Grace", "enterprise/legal_form": "partnership_group", "credit/loan_use": "",
        })

        (section,) = raw_answers(submission)

        self.assertIn("not stored", section["label"])
        self.assertEqual([(f["name"], f["value"]) for f in section["fields"]],
                         [("first_name", "Grace"), ("legal_form", "partnership_group")])


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboApplicationViewTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()
        city = Location.objects.create(code="901", name="Test City", type="R")
        constituency = Location.objects.create(code="90101", name="Central", type="D", parent=city)
        Location.objects.create(code="9010101", name="Ward A", type="W", parent=constituency)
        # The version the submission below was sent with
        store_form("kobo", "aForm", {**read_xlsform(FORM_FILE), "version_uid": "vX"})
        self.individual = Individual(first_name="Grace", last_name="Banda", dob="1995-04-02", json_ext={})
        self.individual.save(username=self.user.username)
        self.submission = KoboSubmission.objects.create(
            source_type="kobo", asset_uid="aForm", kobo_id=5, individual=self.individual, status="IMPORTED", data={
                "applicant/first_name": "Grace", "applicant/res_city": "901", "applicant/res_ward": "9010101",
                "enterprise/legal_form": "partnership_group", "applicant/location_code": "9010101001",
                "training/training_needs": "financial_management ohs", "credit/loan_amount_mwk": "",
                "_id": 5, "__version__": "vX",
            })

    def test_answers_are_shown_with_the_form_labels(self):
        sections = application_sections(self.submission, form_for_submission(self.submission))

        fields = {field["name"]: (field["label"], field["value"]) for s in sections for field in s["fields"]}
        self.assertEqual(sections[0]["label"], "Step 3 of 17 · Applicant information")
        self.assertEqual(fields["legal_form"], ("Legal Form", "Partnership/Group"))
        self.assertEqual(fields["res_city"][1], "Test City")
        self.assertEqual(fields["res_ward"][1], "Ward A")
        self.assertEqual(fields["training_needs"][1], "Financial Management, Occupational, Health and Safety")
        # Not reached, and calculated
        self.assertNotIn("loan_amount_mwk", fields)
        self.assertNotIn("location_code", fields)

    def _info(self, allowed=True, visible=True):
        info = MagicMock()
        info.context.user = self.user
        queryset = Individual.objects.filter(id=self.individual.id) if visible else Individual.objects.none()
        patches = [patch.object(type(self.user), "has_perms", return_value=allowed),
                   patch("individual.gql_queries.IndividualGQLType.get_queryset", return_value=queryset)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return info

    def test_the_query_returns_the_individuals_applications(self):
        applications = Query.resolve_kobo_applications(None, self._info(), individual_id=self.individual.id)

        self.assertEqual([(a.kobo_id, a.status, a.form_version) for a in applications], [(5, "IMPORTED", "2026112402")])
        self.assertTrue(applications[0].sections)

    def test_an_application_without_its_form_version_shows_its_raw_answers(self):
        self.submission.data = {**self.submission.data, "__version__": "vUnknown"}
        self.submission.save()

        (application,) = Query.resolve_kobo_applications(None, self._info(), individual_id=self.individual.id)

        self.assertEqual(application.form_version, "")
        self.assertIn("not stored", application.sections[0]["label"])

    def test_the_query_follows_the_individual_permissions(self):
        self.assertEqual(Query.resolve_kobo_applications(None, self._info(visible=False),
                                                         individual_id=self.individual.id), [])
        with self.assertRaises(PermissionError):
            Query.resolve_kobo_applications(None, self._info(allowed=False), individual_id=self.individual.id)
