import uuid
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from core.test_helpers import LogInHelper
from individual.models import Group, GroupIndividual, Individual, IndividualDataSource, IndividualDataSourceUpload
from msr_etl.models import MsrEtlSyncUnit


class TagSourceRecordsTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()
        self.username = self.user.username
        self.pwp_unit = self._unit("pwp")
        self.ubr_unit = self._unit("ubr")

    def _unit(self, source_type):
        return MsrEtlSyncUnit.objects.create(
            job_uuid=uuid.uuid4(), source_type=source_type, kind=MsrEtlSyncUnit.Kind.INDIVIDUAL,
            unit_type=MsrEtlSyncUnit.UnitType.GVH, unit_code="2130304",
        )

    def _imported(self, unit, json_ext=None, action="import"):
        individual = Individual(first_name="A", last_name="B", dob="1990-01-01", json_ext=json_ext or {})
        individual.save(username=self.username)
        self._link(individual, unit, action)
        return individual

    def _link(self, individual, unit, action="import"):
        upload = IndividualDataSourceUpload(
            source_name=f"{action}_job_{unit.job_uuid}_unit_{unit.id}.csv", source_type="individual import",
        )
        upload.save(username=self.username)
        IndividualDataSource(individual=individual, upload=upload, json_ext={}).save(username=self.username)

    def _household(self, code, *members):
        group = Group(code=code, json_ext={})
        group.save(username=self.username)
        for member in members:
            GroupIndividual(group=group, individual=member).save(username=self.username)
        return group

    def _run(self, *args):
        out = StringIO()
        call_command("tag_source_records", "--username", self.username, *args, stdout=out)
        return out.getvalue()

    def test_reports_without_writing_by_default(self):
        individual = self._imported(self.pwp_unit)

        output = self._run()

        individual.refresh_from_db()
        self.assertNotIn("source_type", individual.json_ext)
        self.assertIn("individuals tagged 1", output)
        self.assertIn("Report only", output)

    def test_apply_tags_individuals_and_their_household(self):
        head, member = self._imported(self.pwp_unit), self._imported(self.pwp_unit, action="update")
        household = self._household("30874398", head, member)

        self._run("--apply")

        head.refresh_from_db(); member.refresh_from_db(); household.refresh_from_db()
        self.assertEqual(head.json_ext["source_type"], "pwp")
        self.assertEqual(member.json_ext["source_type"], "pwp")
        self.assertEqual(household.code, "pwp:30874398")
        self.assertEqual(household.json_ext["source_type"], "pwp")

    def test_skips_people_also_imported_by_another_source(self):
        shared = self._imported(self.pwp_unit)
        self._link(shared, self.ubr_unit)
        household = self._household("30874398", shared)

        output = self._run("--apply")

        shared.refresh_from_db(); household.refresh_from_db()
        self.assertNotIn("source_type", shared.json_ext)
        self.assertEqual(household.code, "30874398")
        self.assertIn("individuals shared with other imports (skipped) 1", output)

    def test_skips_households_with_members_from_other_sources(self):
        pwp_member = self._imported(self.pwp_unit)
        msr_member = self._imported(self.ubr_unit)
        household = self._household("30874398", pwp_member, msr_member)

        output = self._run("--apply")

        household.refresh_from_db()
        self.assertEqual(household.code, "30874398")
        self.assertIn("households with members from other sources (skipped) 1", output)

    def test_leaves_records_tagged_with_another_source(self):
        individual = self._imported(self.pwp_unit, json_ext={"source_type": "upg"})

        output = self._run("--apply")

        individual.refresh_from_db()
        self.assertEqual(individual.json_ext["source_type"], "upg")
        self.assertIn("individuals tagged with another source (skipped) 1", output)

    def test_refuses_to_tag_the_default_source(self):
        with self.assertRaises(CommandError):
            self._run("--source", "ubr")
