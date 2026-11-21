from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.test_helpers import LogInHelper
from individual.models import Individual
from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_form import read_xlsform, store_form
from msr_etl.models import ApplicantDocument, KoboSubmission
from msr_etl.schema import Query
from msr_etl.tests.test_kobo import SOURCES, _submission
from msr_etl.tests.test_kobo_form import FORM_FILE


@patch.object(MsrEtlConfig, "sources", SOURCES)
class KoboSubmissionsQueryTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()
        store_form("kobo", "aForm", {**read_xlsform(FORM_FILE), "version_uid": "v1"})
        self.grace = Individual(first_name="Grace", last_name="Banda", dob="1995-04-02",
                                json_ext={"national_id": "AB12CD34", "sex": "female"})
        self.grace.save(username=self.user.username)
        self.held = self._stored(2, "HELD", self.grace, dob="1996-04-02", first_name="grace ")
        self._stored(1, "IMPORTED", self.grace)
        self._stored(9, "NO_NATIONAL_ID", None, national_id="")
        ApplicantDocument.objects.create(submission=self.held, kobo_attachment_uid="a1", status="STORED")
        ApplicantDocument.objects.create(submission=self.held, kobo_attachment_uid="a2", status="PENDING")

    def _stored(self, kobo_id, status, individual, **answers):
        data = _submission(kobo_id, **answers)
        return KoboSubmission.objects.create(
            source_type="kobo", asset_uid="aForm", kobo_id=kobo_id, status=status, individual=individual,
            national_id=data["applicant/national_id"].replace(" ", "").upper(), data=data)

    def _query(self, allowed=True, visible=None, **arguments):
        info = MagicMock()
        info.context.user = self.user
        visible = Individual.objects.all() if visible is None else visible
        with patch.object(type(self.user), "has_perms", return_value=allowed), \
                patch.object(Individual, "get_queryset", return_value=visible):
            return Query.resolve_kobo_submissions(None, info, **arguments)

    def test_a_held_submission_shows_its_answers_next_to_the_record(self):
        result = self._query(status="HELD")

        (held,) = result.items
        self.assertEqual((held.kobo_id, held.first_name, held.individual_id), (2, "grace ", str(self.grace.id)))
        self.assertEqual((held.documents, held.documents_stored), (2, 1))
        rows = {row["field"]: row for row in held.comparison}
        self.assertEqual(rows["dob"], {"field": "dob", "label": rows["dob"]["label"], "mis": "1995-04-02",
                                       "applied": "1996-04-02", "differs": True})
        # Case and extra spaces don't count
        self.assertFalse(rows["first_name"]["differs"])
        self.assertNotEqual(rows["first_name"]["label"], "first_name")

    def test_the_comparison_follows_the_record_as_it_is_now(self):
        self.grace.dob = "1996-04-02"
        self.grace.save(username=self.user.username)

        (held,) = self._query(status="HELD").items

        self.assertFalse(any(row["differs"] for row in held.comparison))

    def test_counts_cover_every_outcome_and_document(self):
        summary = self._query().summary

        self.assertEqual({k: v for k, v in summary["submissions"].items() if v},
                         {"HELD": 1, "IMPORTED": 1, "NO_NATIONAL_ID": 1})
        self.assertEqual(summary["documents"], {"PENDING": 1, "STORED": 1, "FAILED": 0})

    def test_search_by_national_id_or_kobo_id_and_pages(self):
        self.assertEqual([s.kobo_id for s in self._query(search="ab12 cd").items], [2, 1])
        # A number is a Kobo id, or part of a national ID
        self.assertEqual([s.kobo_id for s in self._query(search="9").items], [9])
        page = self._query(first=1, offset=1)
        self.assertEqual(([s.kobo_id for s in page.items], page.total_count), ([2], 3))

    def test_only_the_review_right_sees_submissions(self):
        with self.assertRaises(PermissionError):
            self._query(allowed=False)
        with patch.object(MsrEtlConfig, "kobo_submission_perms", []), self.assertRaises(PermissionError):
            self._query()

    def test_submissions_of_individuals_outside_the_users_locations_are_hidden(self):
        result = self._query(visible=Individual.objects.none())

        # Only the one linked to no individual
        self.assertEqual([s.kobo_id for s in result.items], [9])
        self.assertEqual(result.summary["submissions"]["HELD"], 0)
