import hashlib
import io
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_reconcile import reconcile
from msr_etl.models import ApplicantDocument, KoboSubmission
from msr_etl.sources import KoboSubmissionSource
from msr_etl.tests.test_kobo import SOURCES

CONTENT = b"%PDF-1.4 statement"


def _row(kobo_id, *uids, deleted=()):
    attachments = [{"uid": uid, "is_deleted": False} for uid in uids]
    attachments += [{"uid": uid, "is_deleted": True} for uid in deleted]
    return {"_id": kobo_id, "_attachments": attachments}


class KoboReconcileTestCase(TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for patcher in (override_settings(MEDIA_ROOT=folder), patch.object(MsrEtlConfig, "sources", SOURCES)):
            patcher.enable() if hasattr(patcher, "enable") else patcher.start()
            self.addCleanup(patcher.disable if hasattr(patcher, "disable") else patcher.stop)
        self.kobo_rows = [_row(1, "a1", deleted=["old"]), _row(2, "b1", "b2")]
        rows = patch.object(KoboSubmissionSource, "_every_row", side_effect=lambda fields: self.kobo_rows)
        rows.start()
        self.addCleanup(rows.stop)

    def _submission(self, kobo_id, status="IMPORTED"):
        return KoboSubmission.objects.create(source_type="kobo", asset_uid="aForm", kobo_id=kobo_id,
                                             status=status, data={})

    def _stored_document(self, submission, uid, content=CONTENT):
        path = default_storage.save(f"kobo_documents/kobo/{submission.kobo_id}/{uid}.pdf", ContentFile(content))
        return ApplicantDocument.objects.create(submission=submission, kobo_attachment_uid=uid, status="STORED",
                                                storage_path=path, sha256=hashlib.sha256(CONTENT).hexdigest())

    def _complete(self):
        one, two = self._submission(1), self._submission(2)
        self._stored_document(one, "a1")
        self._stored_document(two, "b1")
        self._stored_document(two, "b2")
        return one, two

    def test_everything_in_the_mis_is_complete(self):
        self._complete()

        report = reconcile(KoboSubmissionSource("kobo"), checksums=True)

        self.assertTrue(report["complete"])
        self.assertEqual((report["kobo_submissions"], report["mis_submissions"], report["kobo_attachments"]),
                         (2, 2, 3))
        self.assertEqual(report["documents"]["STORED"], 3)

    def test_every_kind_of_gap_is_listed(self):
        one, two = self._complete()
        self.kobo_rows.append(_row(3, "c1"))
        KoboSubmission.objects.filter(kobo_id=2).update(status="RECEIVED")
        ApplicantDocument.objects.filter(kobo_attachment_uid="b2").delete()
        ApplicantDocument.objects.create(submission=two, kobo_attachment_uid="b3", status="FAILED", attempts=5)
        self.kobo_rows[1] = _row(2, "b1", "b2", "b3")
        a1 = ApplicantDocument.objects.get(kobo_attachment_uid="a1")
        default_storage.delete(a1.storage_path)
        b1 = ApplicantDocument.objects.get(kobo_attachment_uid="b1")
        with default_storage.open(b1.storage_path, "wb") as handle:
            handle.write(b"changed")

        report = reconcile(KoboSubmissionSource("kobo"), checksums=True)

        self.assertFalse(report["complete"])
        self.assertEqual(report["not_in_mis"], [3])
        self.assertEqual(report["unfinished"], [2])
        self.assertEqual(report["unregistered"], [(2, "b2")])
        self.assertEqual([(d["uid"], d["status"]) for d in report["not_stored"]], [("b3", "FAILED")])
        self.assertEqual([d["uid"] for d in report["missing_files"]], ["a1"])
        self.assertEqual([d["uid"] for d in report["checksum_mismatch"]], ["b1"])

    def test_checksums_are_read_only_when_asked(self):
        self._complete()
        b1 = ApplicantDocument.objects.get(kobo_attachment_uid="b1")
        with default_storage.open(b1.storage_path, "wb") as handle:
            handle.write(b"changed")

        self.assertTrue(reconcile(KoboSubmissionSource("kobo"))["complete"])
        self.assertFalse(reconcile(KoboSubmissionSource("kobo"), checksums=True)["complete"])

    def test_a_submission_deleted_in_kobo_is_kept_and_not_a_gap(self):
        self._complete()
        self._submission(9)
        report = reconcile(KoboSubmissionSource("kobo"))

        self.assertEqual(report["not_in_kobo"], [9])
        self.assertTrue(report["complete"])

    def test_the_command_reports_and_fails_while_there_are_gaps(self):
        self._complete()
        out = io.StringIO()
        call_command("kobo_reconcile", source="kobo", stdout=out)
        self.assertIn("Complete", out.getvalue())

        self.kobo_rows.append(_row(3, "c1"))
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(CommandError, "Not complete: kobo"):
            path = os.path.join(folder, "report.json")
            try:
                call_command("kobo_reconcile", source="kobo", json_path=path, stdout=out)
            finally:
                (report,) = json.load(open(path))
        self.assertIn("not in mis: 1", out.getvalue())
        self.assertEqual(report["not_in_mis"], [3])

    def test_an_unknown_source_is_refused(self):
        with self.assertRaisesRegex(CommandError, "not a Kobo source"):
            call_command("kobo_reconcile", source="nope", stdout=io.StringIO())
