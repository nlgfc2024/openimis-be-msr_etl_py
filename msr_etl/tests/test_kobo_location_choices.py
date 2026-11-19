import hashlib
import io
import json
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from django.test import TestCase

from core.models import AsyncJob
from core.test_helpers import LogInHelper
from location.models import Location
from msr_etl import jobs, kobo_runs
from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_location_choices import choice_files, choices_out_of_date, push_location_choices
from msr_etl.sources import KoboSubmissionSource
from msr_etl.tests.test_kobo import KOBO_CONFIG

SOURCES = {
    # The test database has other locations too
    "kobo": {**KOBO_CONFIG, "push_location_choices": True, "location_choice_cities": ["901"]},
    "other_form": {**KOBO_CONFIG, "asset_uid": "bForm"},
    "council": {"connector": "excel_locations", "kind": "location"},
}


def _media(name, content, uid):
    return {"uid": uid, "metadata": {"filename": name, "hash": f"md5:{hashlib.md5(content).hexdigest()}"}}


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboLocationChoicesTestCase(TestCase):

    def setUp(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        constituency = Location.objects.create(code="90101", name="Central", type="D", parent=city)
        ward = Location.objects.create(code="9010101", name="Ward A", type="W", parent=constituency)
        Location.objects.create(code="9010101001", name="Area 1", type="V", parent=ward)
        self.files = choice_files(["901"])

    def _kobo(self, media, version_id="v2", deployed="v2", active=True):
        asset = {"version_id": version_id, "deployed_version_id": deployed}
        if active is not None:
            asset["deployment__active"] = active
        replies = {"files/": {"results": media}, "": asset}
        get = patch.object(KoboSubmissionSource, "_get", side_effect=lambda params, path="data/": replies[path])
        request = patch.object(KoboSubmissionSource, "request")
        self.addCleanup(get.stop)
        self.addCleanup(request.stop)
        get.start()
        return request.start()

    def test_the_files_are_the_mis_lists(self):
        self.assertEqual(self.files["wards.csv"].decode(), "name,label,city\n9010101,Ward A,901\n")
        self.assertEqual(self.files["neighbourhoods.csv"].decode(),
                         "name,label,city,ward,location_name\n9010101001,Area 1,901,9010101,Area 1\n")

    def test_nothing_changes_in_kobo_when_the_lists_are_the_same(self):
        request = self._kobo([_media(name, content, f"u{n}") for n, (name, content) in enumerate(self.files.items())])

        result = push_location_choices("kobo")

        self.assertEqual((result["updated"], result["redeployed"]), ([], False))
        request.assert_not_called()
        self.assertFalse(choices_out_of_date("kobo"))

    def test_a_changed_list_replaces_the_file_and_redeploys(self):
        media = [_media("cities.csv", self.files["cities.csv"], "u1"), _media("wards.csv", b"old", "u2")]
        request = self._kobo(media)

        self.assertTrue(choices_out_of_date("kobo"))
        result = push_location_choices("kobo")

        self.assertEqual((result["updated"], result["unchanged"], result["redeployed"]),
                         (["wards.csv", "neighbourhoods.csv"], ["cities.csv"], True))
        methods = [(c.args[0], c.args[1]) for c in request.call_args_list]
        self.assertEqual(methods, [("DELETE", "files/u2/"), ("POST", "files/"), ("POST", "files/"),
                                   ("PATCH", "deployment/")])
        upload = request.call_args_list[1].kwargs
        self.assertEqual(json.loads(upload["data"]["metadata"]), {"filename": "wards.csv"})
        self.assertEqual(upload["files"]["content"][1], self.files["wards.csv"])
        self.assertEqual(request.call_args_list[3].kwargs["json"], {"active": True, "version_id": "v2"})

    def test_a_closed_form_stays_closed_when_it_gets_new_lists(self):
        request = self._kobo([], active=False)

        result = push_location_choices("kobo")

        self.assertTrue(result["redeployed"])
        self.assertEqual(request.call_args_list[-1].kwargs["json"], {"active": False, "version_id": "v2"})

    def test_a_form_whose_state_kobo_does_not_give_is_not_redeployed(self):
        request = self._kobo([], active=None)

        result = push_location_choices("kobo")

        self.assertFalse(result["redeployed"])
        self.assertIn("whether the form is open", result["warning"])
        self.assertNotIn("PATCH", [c.args[0] for c in request.call_args_list])

    def test_a_form_with_undeployed_changes_is_not_redeployed(self):
        request = self._kobo([], version_id="draft", deployed="v2")

        result = push_location_choices("kobo")

        self.assertFalse(result["redeployed"])
        self.assertIn("not deployed", result["warning"])
        self.assertNotIn("PATCH", [c.args[0] for c in request.call_args_list])

    def test_the_push_can_use_its_own_editor_token(self):
        from msr_etl.kobo_location_choices import _editor

        self.assertEqual(_editor("kobo").auth_provider.get_auth_header(), {"Authorization": "Token secret-token"})
        sources = {"kobo": {**SOURCES["kobo"], "location_choices_auth_token": "editor-token"}}
        with patch.object(MsrEtlConfig, "sources", sources):
            self.assertEqual(_editor("kobo").auth_provider.get_auth_header(), {"Authorization": "Token editor-token"})

    def test_an_empty_list_is_refused(self):
        Location.objects.filter(code="9010101001").delete()
        with self.assertRaisesRegex(ValueError, "No locations for neighbourhoods.csv"):
            choice_files(["901"])

    def test_the_command_writes_or_pushes(self):
        request = self._kobo([])
        out = io.StringIO()
        call_command("export_kobo_location_choices", push="kobo", stdout=out)

        self.assertIn("redeployed: yes", out.getvalue())
        self.assertEqual(request.call_count, 4)


@patch.object(MsrEtlConfig, "sources", SOURCES)
class LocationChoicesRunsTestCase(TestCase):

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

    def test_only_forms_set_to_receive_the_lists_get_them(self):
        self.assertEqual(kobo_runs.location_choice_sources(), ["kobo"])

        kobo_runs.start_location_choices_pushes(user=self.user)
        kobo_runs.start_location_choices_pushes(user=self.user)  # still running: nothing new

        self.assertEqual([(job.job_type, job.params) for job in self.jobs],
                         [("kobo_location_choices_push", {"source_type": "kobo"})])

    def test_a_push_does_not_block_the_sources_import(self):
        kobo_runs.start_location_choices_push("kobo", user=self.user)
        kobo_runs.start_import("kobo", user=self.user)

        self.assertEqual(len(self.jobs), 2)

    @patch("msr_etl.kobo_location_choices.choices_out_of_date")
    def test_the_hourly_check_pushes_only_a_form_behind_the_lists(self, out_of_date):
        with patch.object(MsrEtlConfig, "sink_import_username", self.user.username):
            out_of_date.return_value = False
            kobo_runs.check_location_choices()
            out_of_date.return_value = True
            kobo_runs.check_location_choices()

        self.assertEqual(len(self.jobs), 1)

    @patch("msr_etl.jobs._run_staged_import")
    @patch("msr_etl.jobs.UBRLocationService")
    @patch("msr_etl.kobo_runs.start_location_choices_pushes")
    def test_a_council_list_import_sends_the_lists_to_kobo(self, pushes, service, _run):
        reporter = MagicMock()
        for source_type, expected in (("council", [call(user=reporter.job.user)]), ("ubr", [])):
            pushes.reset_mock()
            service.return_value.source.source_type = source_type
            jobs.run_ubr_locations_import(reporter, source_type=source_type)
            self.assertEqual(pushes.call_args_list, expected)

    @patch("msr_etl.kobo_location_choices.push_location_choices")
    def test_the_job_reports_a_form_it_could_not_redeploy(self, push):
        reporter = MagicMock()
        push.return_value = {"updated": ["wards.csv"], "unchanged": [], "redeployed": False, "warning": "redeploy it"}

        jobs.run_kobo_location_choices_push(reporter, source_type="kobo")

        reporter.partial.assert_called_once_with(error="redeploy it")
        reporter.advance.assert_called_once_with(k=1, files_updated=1, redeployed=0)
