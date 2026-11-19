"""
Applicant documents: the files attached to Kobo submissions, copied into the MIS file storage. Every submission's
attachments are registered when it is stored, whatever its outcome (held, no national ID...), then downloaded by a
separate job, since Kobo is switched off after the application period. Nothing is overwritten or deleted.
"""
import hashlib
import logging
import os
import re
import tempfile

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
    return (f"{root}/{document.submission.source_type}/{document.submission.kobo_id}/"
            f"{document_type}_{document.kobo_attachment_uid}{extension}")


def download_document(kobo, document):
    """Fetch one document from Kobo and store it; raises on any failure (the caller records it)."""
    submission = document.submission
    response = kobo.request("GET", f"data/{submission.kobo_id}/attachments/{document.kobo_attachment_uid}/",
                            expected=(200,), headers={"Accept": "*/*"}, stream=True)
    digest, size = hashlib.sha256(), 0
    with tempfile.TemporaryFile() as buffer:
        for chunk in response.iter_content(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            buffer.write(chunk)
        buffer.seek(0)
        # save() picks a new name if the path is taken, so an existing file is never overwritten
        path = default_storage.save(storage_path(document), File(buffer))
    ApplicantDocument.objects.filter(id=document.id).update(
        status=ApplicantDocument.Status.STORED, storage_path=path, size=size, sha256=digest.hexdigest(),
        mimetype=document.mimetype or response.headers.get("Content-Type", "").split(";")[0],
        error="", stored_at=timezone.now(), updated_at=timezone.now(),
    )


def record_failure(document, error):
    ApplicantDocument.objects.filter(id=document.id).update(
        status=ApplicantDocument.Status.FAILED, attempts=F("attempts") + 1, error=str(error)[:2000],
        updated_at=timezone.now(),
    )
