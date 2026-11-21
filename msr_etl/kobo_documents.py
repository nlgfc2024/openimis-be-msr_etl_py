"""
Applicant documents: the files attached to Kobo submissions, copied into the MIS file storage. Every submission's
attachments are registered when it is stored, whatever its outcome (held, no national ID...), then downloaded by a
separate job, since Kobo is switched off after the application period. Nothing is overwritten or deleted.
"""
import csv
import datetime
import hashlib
import io
import logging
import os
import re
import tempfile

import openpyxl
import requests
from django.core.files import File
from django.core.files.storage import default_storage
from django.db.models import F, Q
from django.utils import timezone

from msr_etl.apps import MsrEtlConfig
from msr_etl.models import ApplicantDocument, KoboSubmission

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024
# Shown in the browser; anything else is downloaded
INLINE_MIMETYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
# Shown in the page as a table (read on request; the stored file is kept as sent)
TABLE_MIMETYPES = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv"}
_UID = re.compile(r"[A-Za-z0-9]+")


class DocumentError(Exception):
    """Something wrong with this one document: counted against its attempts."""


def is_kobo_failure(exc):
    """Kobo itself failing (unreachable, refusing the token, erroring), not the document: no attempt is counted."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status in (401, 403, 429) or status >= 500
    return isinstance(exc, requests.RequestException) or isinstance(exc.__cause__, requests.RequestException)


def _setting(name, default):
    return int(getattr(MsrEtlConfig, name, None) or default)


def register_documents(source_type, asset_uid, rows):
    """An ApplicantDocument (PENDING) for every attachment of these stored submissions; again is a no-op."""
    submissions = {s.kobo_id: s for s in KoboSubmission.objects.filter(
        source_type=source_type, asset_uid=asset_uid, kobo_id__in=[row["_id"] for row in rows if "_id" in row])}
    documents = []
    for row in rows:
        submission = submissions.get(row.get("_id"))
        for attachment in (row.get("_attachments") or []) if submission else []:
            if attachment.get("is_deleted") or not attachment.get("uid"):
                continue
            question = str(attachment.get("question_xpath") or "")
            documents.append(ApplicantDocument(
                submission=submission,
                individual=submission.individual,
                kobo_attachment_uid=str(attachment["uid"]),
                question=question,
                document_type=question.split("/")[-1],
                filename=_basename(attachment),
                mimetype=str(attachment.get("mimetype") or ""),
            ))
    ApplicantDocument.objects.bulk_create(documents, ignore_conflicts=True, batch_size=500)


def link_documents(submission_filter, individual):
    """Give a submission's documents its Individual (the one it created, updated or was held from)."""
    ApplicantDocument.objects.filter(**{f"submission__{key}": value for key, value in submission_filter.items()}) \
        .update(individual=individual)


def _basename(attachment):
    name = str(attachment.get("media_file_basename") or os.path.basename(str(attachment.get("filename") or "")))
    return name[:255]


def to_download(source_type):
    """Documents still to fetch: pending, or failed fewer than kobo_document_max_attempts times."""
    max_attempts = int(MsrEtlConfig.kobo_document_max_attempts or 5)
    return ApplicantDocument.objects.filter(submission__source_type=source_type).filter(
        Q(status=ApplicantDocument.Status.PENDING)
        | Q(status=ApplicantDocument.Status.FAILED, attempts__lt=max_attempts)
    ).select_related("submission").order_by("created_at")


def storage_path(document):
    """Where a document goes: by source, submission and attachment, never by anything personal."""
    root = str(MsrEtlConfig.kobo_documents_path or "kobo_documents").strip("/")
    extension = os.path.splitext(document.filename)[1].lower()
    extension = extension if re.fullmatch(r"\.[a-z0-9]{1,8}", extension or "") else ""
    document_type = re.sub(r"[^A-Za-z0-9_-]", "_", document.document_type or "document")
    uid = re.sub(r"[^A-Za-z0-9]", "_", document.kobo_attachment_uid)
    return f"{root}/{document.submission.source_type}/{document.submission.kobo_id}/{document_type}_{uid}{extension}"


def download_document(kobo, document):
    """Fetch one document from Kobo and store it. Raises on any failure: DocumentError or a Kobo error with its
    status for the caller to tell a bad document from Kobo being down (is_kobo_failure)."""
    submission = document.submission
    if not _UID.fullmatch(document.kobo_attachment_uid or ""):
        raise DocumentError(f"Unexpected Kobo attachment uid {document.kobo_attachment_uid!r}")
    max_bytes = _setting("kobo_document_max_bytes", 100 * 1024 * 1024)
    response = kobo.request("GET", f"data/{submission.kobo_id}/attachments/{document.kobo_attachment_uid}/",
                            expected=(200,), headers={"Accept": "*/*"}, stream=True)
    digest, size = hashlib.sha256(), 0
    with tempfile.TemporaryFile() as buffer:
        for chunk in response.iter_content(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise DocumentError(f"Larger than kobo_document_max_bytes ({max_bytes} bytes)")
            buffer.write(chunk)
        expected = response.headers.get("Content-Length")
        if size == 0:
            raise DocumentError("Kobo sent an empty file")
        if expected and expected.isdigit() and int(expected) != size:
            raise DocumentError(f"Kobo sent {size} bytes of {expected}")
        buffer.seek(0)
        # save() picks a new name if the path is taken, so an existing file is never overwritten
        path = default_storage.save(storage_path(document), File(buffer))
    ApplicantDocument.objects.filter(id=document.id).update(
        status=ApplicantDocument.Status.STORED, storage_path=path, size=size, sha256=digest.hexdigest(),
        mimetype=document.mimetype or response.headers.get("Content-Type", "").split(";")[0],
        error="", stored_at=timezone.now(), updated_at=timezone.now(),
    )


class TableError(Exception):
    pass


def read_table(document):
    """A stored spreadsheet's sheets as rows of text, for showing in the page; capped at kobo_table_max_rows rows
    and kobo_table_max_columns columns per sheet. The stored file is left as it is."""
    if document.mimetype not in TABLE_MIMETYPES:
        raise TableError("This type of file can't be shown as a table")
    if (document.size or 0) > _setting("kobo_table_max_bytes", 20 * 1024 * 1024):
        raise TableError("The file is too large to be shown as a table")
    with default_storage.open(document.storage_path, "rb") as handle:
        if document.mimetype == "text/csv":
            text = io.TextIOWrapper(handle, encoding="utf-8-sig", errors="replace")
            return [_sheet("", csv.reader(text))]
        try:
            # openpyxl parses with defusedxml when it is installed
            workbook = openpyxl.load_workbook(handle, read_only=True, data_only=True)
        except Exception as exc:
            raise TableError("The file could not be read as a spreadsheet") from exc
        try:
            return [_sheet(sheet.title, sheet.iter_rows(values_only=True))
                    for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        finally:
            workbook.close()


def _sheet(name, rows):
    max_rows, max_columns = _setting("kobo_table_max_rows", 1000), _setting("kobo_table_max_columns", 50)
    cells, truncated = [], False
    for number, row in enumerate(rows):
        if number >= max_rows:
            truncated = True
            break
        truncated = truncated or len(row) > max_columns
        cells.append([_text(value) for value in row[:max_columns]])
    # Blank rows and columns around the content are dropped; blank rows inside it are kept (unfilled template rows)
    while cells and not any(cells[-1]):
        cells.pop()
    while cells and not any(cells[0]):
        cells.pop(0)
    width = max((max((i + 1 for i, value in enumerate(row) if value), default=0) for row in cells), default=0)
    first = min((next(i for i, value in enumerate(row) if value) for row in cells if any(row)), default=0)
    return {"name": name, "rows": [(row + [""] * width)[first:width] for row in cells], "truncated": truncated}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime) and value.time() == datetime.time():
        return value.date().isoformat()
    return str(value)


def record_failure(document, error):
    ApplicantDocument.objects.filter(id=document.id).update(
        status=ApplicantDocument.Status.FAILED, attempts=F("attempts") + 1, error=str(error)[:2000],
        updated_at=timezone.now(),
    )


def requeue_failed(source_type=None):
    """Put documents that used up their attempts back in the queue, with fresh attempts; returns how many."""
    failed = ApplicantDocument.objects.filter(status=ApplicantDocument.Status.FAILED)
    if source_type:
        failed = failed.filter(submission__source_type=source_type)
    return failed.update(status=ApplicantDocument.Status.PENDING, attempts=0, updated_at=timezone.now())
