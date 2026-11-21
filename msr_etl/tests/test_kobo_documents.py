import hashlib
import io
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import requests

import openpyxl
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import AsyncJob
from core.services.asyncJobServices import JobCancelled
from core.test_helpers import LogInHelper
from individual.models import Individual
from msr_etl import jobs, kobo_runs
from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_documents import (
    DocumentError, download_document, read_table, record_failure, register_documents, storage_path, to_download,
)
from msr_etl.kobo_form import read_xlsform, store_form
from msr_etl.models import ApplicantDocument, ApplicantDocumentAccess, KoboSubmission
from msr_etl.schema import Query
from msr_etl.sources import KoboSubmissionSource
from msr_etl.tests.test_kobo import SOURCES, _submission
from msr_etl.tests.test_kobo_form import FORM_FILE
from msr_etl.views import applicant_document, applicant_document_table


def _workbook():
    """A filled-in template: a blank row above, a header, figures, and a hidden sheet."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append([])
    sheet.append([None, "ASSET", "ESTIMATED VALUE (MWK)"])
    sheet.append([None, "Sewing machine", 250000.0])
    sheet.append([None, None, None])
    sheet.append([None, "Generator", 1250000.5])
    workbook.create_sheet("Lists").sheet_state = "hidden"
    content = io.BytesIO()
    workbook.save(content)
    return content.getvalue()


PDF, XLSX = b"%PDF-1.4 statement", _workbook()


def _attachment(uid, question, name, mimetype, deleted=False):
    return {"uid": uid, "question_xpath": question, "media_file_basename": name, "mimetype": mimetype,
            "filename": f"super_admin/attachments/x/y/{name}", "is_deleted": deleted,
            "download_url": "http://kobo.test/ignored"}


def _with_attachments(kobo_id):
    return _submission(kobo_id, _attachments=[
        _attachment("attPDF", "documents/doc_bank_statement", "statement.pdf", "application/pdf"),
        _attachment("attXLS", "performance/performance_template", "performance.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        _attachment("attOLD", "documents/doc_other", "replaced.pdf", "application/pdf", deleted=True),
    ])


def _kobo_error(status_code):
    error = KoboSubmissionSource.Error(f"Kobo returned {status_code}")
    error.status_code = status_code
    return error


def _kobo_file(content, content_type="application/pdf"):
    response = MagicMock(headers={"Content-Type": content_type})
    response.iter_content.return_value = [content[:5], content[5:]]
    return response


class _StorageTestCase(TestCase):
    """Files go to a throwaway folder, not the instance's file storage."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for patcher in (override_settings(MEDIA_ROOT=folder), patch.object(MsrEtlConfig, "sources", SOURCES)):
            patcher.enable() if hasattr(patcher, "enable") else patcher.start()
            self.addCleanup(patcher.disable if hasattr(patcher, "disable") else patcher.stop)
        self.user = LogInHelper().get_or_create_user_api()

    def _store(self, kobo_id=1):
        row = _with_attachments(kobo_id)
        KoboSubmission.objects.create(source_type="kobo", asset_uid="aForm", kobo_id=kobo_id, data=row)
        register_documents("kobo", "aForm", [row])
        return {d.document_type: d for d in ApplicantDocument.objects.select_related("submission")}


class RegisterAndDownloadTestCase(_StorageTestCase):

    def test_every_attachment_is_registered_once_and_deleted_ones_are_not(self):
        documents = self._store()
        register_documents("kobo", "aForm", [_with_attachments(1)])

        self.assertEqual(sorted(documents), ["doc_bank_statement", "performance_template"])
        statement = documents["doc_bank_statement"]
        self.assertEqual((statement.filename, statement.mimetype, statement.status, statement.question),
                         ("statement.pdf", "application/pdf", "PENDING", "documents/doc_bank_statement"))
        self.assertEqual(ApplicantDocument.objects.count(), 2)

    def test_the_source_registers_the_documents_of_what_it_stores(self):
        with patch("msr_etl.sources.kobo_source.get_with_resilience") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"results": [_with_attachments(4)]}
            KoboSubmissionSource("kobo").fetch_unit({"kobo_ids": [4], "stored": False})

        self.assertEqual(ApplicantDocument.objects.filter(submission__kobo_id=4).count(), 2)

    def test_a_download_is_stored_with_its_checksum_under_a_path_with_nothing_personal(self):
        statement = self._store()["doc_bank_statement"]
        kobo = MagicMock()
        kobo.request.return_value = _kobo_file(PDF)

        download_document(kobo, statement)

        statement.refresh_from_db()
        method, path = kobo.request.call_args.args
        self.assertEqual((method, path), ("GET", "data/1/attachments/attPDF/"))
        self.assertEqual(kobo.request.call_args.kwargs["headers"], {"Accept": "*/*"})
        self.assertEqual((statement.status, statement.size, statement.sha256),
                         ("STORED", len(PDF), hashlib.sha256(PDF).hexdigest()))
        self.assertEqual(statement.storage_path, "kobo_documents/kobo/1/doc_bank_statement_attPDF.pdf")
        self.assertNotIn("AB12CD34", statement.storage_path)
        with default_storage.open(statement.storage_path, "rb") as stored:
            self.assertEqual(stored.read(), PDF)

    def test_an_existing_file_is_never_overwritten(self):
        statement = self._store()["doc_bank_statement"]
        default_storage.save(storage_path(statement), ContentFile(b"already here"))
        kobo = MagicMock()
        kobo.request.return_value = _kobo_file(PDF)

        download_document(kobo, statement)

        statement.refresh_from_db()
        self.assertNotEqual(statement.storage_path, storage_path(statement))
        with default_storage.open(storage_path(statement), "rb") as untouched:
            self.assertEqual(untouched.read(), b"already here")

    def test_failures_are_retried_up_to_the_limit(self):
        statement = self._store()["doc_bank_statement"]
        with patch.object(MsrEtlConfig, "kobo_document_max_attempts", 2):
            record_failure(statement, "Kobo returned 502")
            self.assertIn(statement, to_download("kobo"))
            record_failure(statement, "Kobo returned 502")
            self.assertNotIn(statement, to_download("kobo"))
        statement.refresh_from_db()
        self.assertEqual((statement.status, statement.attempts, statement.error), ("FAILED", 2, "Kobo returned 502"))

    def test_the_job_stores_what_it_can_and_reports_the_rest(self):
        self._store()
        reporter = MagicMock()
        replies = {"attPDF": _kobo_file(PDF)}

        def answer(method, path, **kwargs):
            uid = path.rstrip("/").split("/")[-1]
            if uid not in replies:
                # Gone from Kobo: this document's problem
                raise _kobo_error(404)
            return replies[uid]

        with patch.object(KoboSubmissionSource, "request", side_effect=answer):
            jobs.run_kobo_documents_download(reporter, source_type="kobo")

        self.assertEqual(dict(ApplicantDocument.objects.values_list("document_type", "status")),
                         {"doc_bank_statement": "STORED", "performance_template": "FAILED"})
        reporter.partial.assert_called_once()

    def _run(self, side_effect, reporter=None):
        reporter = reporter or MagicMock()
        with patch.object(KoboSubmissionSource, "request", side_effect=side_effect) as request:
            jobs.run_kobo_documents_download(reporter, source_type="kobo")
        return reporter, request

    def test_kobo_being_down_stops_the_run_and_uses_no_attempt(self):
        self._store()
        connection_error = KoboSubmissionSource.Error("refused")
        connection_error.__cause__ = requests.ConnectionError("refused")
        for failure in (_kobo_error(503), _kobo_error(401), connection_error, requests.ConnectionError("reset")):
            reporter, request = self._run(failure)

            self.assertEqual(request.call_count, 1)
            reporter.fail.assert_called_once()
            self.assertEqual(set(ApplicantDocument.objects.values_list("status", "attempts")), {("PENDING", 0)})

    def test_a_cancel_after_a_download_leaves_the_document_stored(self):
        self._store()
        reporter = MagicMock()
        reporter.advance.side_effect = JobCancelled("cancelled")

        with self.assertRaises(JobCancelled):
            self._run(lambda method, path, **kwargs: _kobo_file(PDF), reporter=reporter)

        statuses = sorted(ApplicantDocument.objects.values_list("status", flat=True))
        self.assertEqual(statuses, ["PENDING", "STORED"])

    def test_an_unexpected_attachment_uid_is_never_requested(self):
        statement = self._store()["doc_bank_statement"]
        statement.kobo_attachment_uid = "../x"
        kobo = MagicMock()

        with self.assertRaises(DocumentError):
            download_document(kobo, statement)
        kobo.request.assert_not_called()
        self.assertNotIn("..", storage_path(statement))

    def test_a_file_over_the_size_limit_is_refused(self):
        statement = self._store()["doc_bank_statement"]
        kobo = MagicMock()
        kobo.request.return_value = _kobo_file(PDF)

        with patch.object(MsrEtlConfig, "kobo_document_max_bytes", 5), self.assertRaises(DocumentError):
            download_document(kobo, statement)
        self.assertFalse(default_storage.exists(storage_path(statement)))

    def test_an_empty_or_short_download_is_refused(self):
        statement = self._store()["doc_bank_statement"]
        empty, short = _kobo_file(b""), _kobo_file(PDF)
        short.headers["Content-Length"] = str(len(PDF) + 10)
        for response, message in ((empty, "empty file"), (short, "bytes of")):
            kobo = MagicMock()
            kobo.request.return_value = response
            with self.assertRaisesRegex(DocumentError, message):
                download_document(kobo, statement)
        self.assertFalse(default_storage.exists(storage_path(statement)))

    def test_used_up_documents_can_be_put_back_in_the_queue(self):
        statement = self._store()["doc_bank_statement"]
        with patch.object(MsrEtlConfig, "kobo_document_max_attempts", 1):
            record_failure(statement, "Kobo returned 404")
            self.assertNotIn(statement, to_download("kobo"))
            call_command("kobo_documents_retry", source="kobo", stdout=io.StringIO())
            self.assertIn(statement, to_download("kobo"))
        statement.refresh_from_db()
        self.assertEqual((statement.status, statement.attempts), ("PENDING", 0))


class DocumentRunsTestCase(_StorageTestCase):

    def setUp(self):
        super().setUp()
        self.jobs = []

        def schedule(*args, **kwargs):
            job = AsyncJob.objects.create(module="msr_etl", job_type=kwargs["job_type"], task=args[0],
                                          user=self.user, params=kwargs["params"], status=AsyncJob.Status.QUEUED)
            self.jobs.append(job)
            return job.id

        patcher = patch("msr_etl.kobo_runs.run_as_scheduled_job", side_effect=schedule)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_download_starts_only_when_there_is_something_to_fetch(self):
        kobo_runs.start_documents_downloads(["kobo"], user=self.user)
        self.assertEqual(self.jobs, [])

        self._store()
        kobo_runs.start_documents_downloads(["kobo"], user=self.user)
        kobo_runs.start_documents_downloads(["kobo"], user=self.user)  # still running: nothing new

        self.assertEqual([(job.job_type, job.params) for job in self.jobs],
                         [("kobo_documents_download", {"source_type": "kobo"})])

    @patch("msr_etl.jobs._run_staged_import")
    @patch("msr_etl.jobs.UBRIndividualService")
    @patch("msr_etl.kobo_runs.start_documents_downloads")
    def test_a_kobo_import_is_followed_by_its_documents(self, downloads, service, _run):
        reporter = MagicMock()
        service.return_value.source.source_type = "kobo"
        jobs.run_ubr_individuals_import(reporter, source_type="kobo")

        downloads.assert_called_once_with(["kobo"], user=reporter.job.user)


class ApplicantDocumentAccessTestCase(_StorageTestCase):

    def setUp(self):
        super().setUp()
        documents = self._store()
        self.individual = Individual(first_name="Grace", last_name="Banda", dob="1995-04-02", json_ext={})
        self.individual.save(username=self.user.username)
        ApplicantDocument.objects.update(individual=self.individual)
        for document in documents.values():
            kobo = MagicMock()
            kobo.request.return_value = _kobo_file(PDF if document.document_type == "doc_bank_statement" else XLSX)
            download_document(kobo, document)
        self.statement = ApplicantDocument.objects.get(document_type="doc_bank_statement")
        self.template = ApplicantDocument.objects.get(document_type="performance_template")

    def _open(self, document, individual_id=None, allowed=True, visible=True, view=applicant_document):
        request = APIRequestFactory().get("/api/msr_etl/individuals/x/documents/y/")
        force_authenticate(request, user=self.user)
        visible_individuals = Individual.objects.all() if visible else Individual.objects.none()
        with patch.object(type(self.user), "has_perms", return_value=allowed), \
                patch.object(Individual, "get_queryset", return_value=visible_individuals):
            return view(request, individual_id=individual_id or self.individual.id, document_id=document.id)

    def test_a_pdf_opens_in_the_browser_and_the_opening_is_logged(self):
        response = self._open(self.statement)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual((response["Content-Type"], response["X-Content-Type-Options"]), ("application/pdf", "nosniff"))
        self.assertEqual(list(ApplicantDocumentAccess.objects.values_list("username", "disposition")),
                         [(self.user.username, "inline")])

    def test_other_files_are_downloaded(self):
        response = self._open(self.template)

        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertIn("performance.xlsx", response["Content-Disposition"])

    def test_a_spreadsheet_is_shown_as_its_rows_and_the_showing_is_logged(self):
        response = self._open(self.template, view=applicant_document_table)

        self.assertEqual(response.status_code, 200)
        # The blank row and column around the content go; the blank row inside it stays; hidden sheets aren't shown
        self.assertEqual(response.data["sheets"], [{"name": "Sheet1", "truncated": False, "rows": [
            ["ASSET", "ESTIMATED VALUE (MWK)"], ["Sewing machine", "250000"], ["", ""], ["Generator", "1250000.5"],
        ]}])
        self.assertEqual(list(ApplicantDocumentAccess.objects.values_list("disposition", flat=True)), ["table"])

    def test_the_table_limits_come_from_the_configuration(self):
        with patch.object(MsrEtlConfig, "kobo_table_max_rows", 2), \
                patch.object(MsrEtlConfig, "kobo_table_max_columns", 2):
            (sheet,) = read_table(self.template)

        self.assertEqual((sheet["rows"], sheet["truncated"]), ([["ASSET"]], True))

    def test_only_spreadsheets_are_shown_as_tables(self):
        response = self._open(self.statement, view=applicant_document_table)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ApplicantDocumentAccess.objects.exists())

    def test_a_table_follows_the_same_right_and_individual(self):
        other = Individual(first_name="Other", last_name="Person", dob="1990-01-01", json_ext={})
        other.save(username=self.user.username)

        self.assertEqual(self._open(self.template, allowed=False, view=applicant_document_table).status_code, 403)
        self.assertEqual(self._open(self.template, individual_id=other.id, view=applicant_document_table).status_code,
                         404)
        self.assertFalse(ApplicantDocumentAccess.objects.exists())

    def test_only_the_document_right_opens_documents(self):
        self.assertEqual(self._open(self.statement, allowed=False).status_code, 403)
        with patch.object(MsrEtlConfig, "applicant_document_perms", []):
            self.assertEqual(self._open(self.statement).status_code, 403)
        self.assertFalse(ApplicantDocumentAccess.objects.exists())

    def test_a_document_is_reached_only_through_its_own_visible_individual(self):
        other = Individual(first_name="Other", last_name="Person", dob="1990-01-01", json_ext={})
        other.save(username=self.user.username)

        # Not found either way, so a document id reveals nothing
        self.assertEqual(self._open(self.statement, individual_id=other.id).status_code, 404)
        self.assertEqual(self._open(self.statement, visible=False).status_code, 404)
        self.assertFalse(ApplicantDocumentAccess.objects.exists())

    def test_the_query_lists_the_documents_with_their_questions(self):
        store_form("kobo", "aForm", {**read_xlsform(FORM_FILE), "version_uid": "v1"})
        KoboSubmission.objects.update(data={**_with_attachments(1), "__version__": "v1"})
        info = MagicMock()
        info.context.user = self.user
        with patch.object(type(self.user), "has_perms", return_value=True), \
                patch("individual.gql_queries.IndividualGQLType.get_queryset", return_value=Individual.objects.all()):
            documents = {d.document_type: d for d in Query.resolve_applicant_documents(
                None, info, individual_id=self.individual.id)}

        statement = documents["doc_bank_statement"]
        self.assertEqual(statement.label, "Latest Business Bank Statement, last 3 months (PDF)")
        self.assertTrue(statement.can_open)
        self.assertEqual(statement.url, f"msr_etl/individuals/{self.individual.id}/documents/{self.statement.id}/")
        self.assertIsNone(statement.table_url)
        template = documents["performance_template"]
        self.assertEqual(template.table_url, f"msr_etl/individuals/{self.individual.id}/documents/{self.template.id}/table/")
