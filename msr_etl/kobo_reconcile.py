"""
The check before Kobo is switched off: does the MIS hold every submission and every file Kobo has? Read-only;
it lists the gaps and what fixes each.
"""
import hashlib

from django.core.files.storage import default_storage

from msr_etl.models import ApplicantDocument, KoboSubmission

_CHUNK = 1024 * 1024
# What to do about each gap, shown with it
FIXES = {
    "not_in_mis": "Run the import (Pull Data on the Individuals import tab).",
    "unfinished": "Run the import again; these were stored but not processed.",
    "unregistered": "Run the import again: storing a submission registers its files.",
    "not_stored": "Wait for the document download, or run manage.py kobo_documents_retry if they used up "
                  "their attempts.",
    "missing_files": "Registered as stored but not in the file storage: restore them from the backup.",
    "wrong_size": "The stored file is empty or not the size downloaded: restore it from the backup.",
    "checksum_mismatch": "The stored file differs from what was downloaded: restore it from the backup.",
}
GAPS = tuple(FIXES)


def reconcile(source, checksums=False):
    """A report on one Kobo source (a KoboSubmissionSource): counts on both sides and every gap."""
    stored = KoboSubmission.objects.filter(source_type=source.source_type, asset_uid=source.asset_uid)
    kobo_attachments = source.attachment_uids()
    stored_ids = set(stored.values_list("kobo_id", flat=True))
    documents = ApplicantDocument.objects.filter(submission__in=stored).select_related("submission")
    registered = {(d.submission.kobo_id, d.kobo_attachment_uid) for d in documents}
    in_kobo = {(kobo_id, uid) for kobo_id, uids in kobo_attachments.items() for uid in uids}

    report = {
        "source": source.source_type,
        "asset_uid": source.asset_uid,
        # Still open, more may come after this check
        "form_open": source.deployment_active(),
        "kobo_submissions": len(kobo_attachments),
        "mis_submissions": len(stored_ids),
        "outcomes": dict(sorted((status, stored.filter(status=status).count())
                                for status in KoboSubmission.Status.values)),
        "kobo_attachments": len(in_kobo),
        "documents": {status: documents.filter(status=status).count() for status in ApplicantDocument.Status.values},
        "not_in_mis": sorted(set(kobo_attachments) - stored_ids),
        "unfinished": sorted(stored.filter(status=KoboSubmission.Status.RECEIVED).values_list("kobo_id", flat=True)),
        # Only submissions the MIS has: the files of the others are registered when they are imported
        "unregistered": sorted((kobo_id, uid) for kobo_id, uid in in_kobo - registered if kobo_id in stored_ids),
        "not_stored": [_describe(d) for d in documents.exclude(status=ApplicantDocument.Status.STORED)
                       .order_by("submission__kobo_id", "kobo_attachment_uid")],
        "missing_files": [],
        "wrong_size": [],
        "checksum_mismatch": [],
        # Kept in the MIS; Kobo no longer has them (deleted there), which is not a gap
        "not_in_kobo": sorted(stored_ids - set(kobo_attachments)),
    }
    for document in documents.filter(status=ApplicantDocument.Status.STORED).order_by("submission__kobo_id"):
        if not document.storage_path or not default_storage.exists(document.storage_path):
            report["missing_files"].append(_describe(document))
        elif not document.size or default_storage.size(document.storage_path) != document.size:
            # Cheap, so on every run: an empty or cut-short file isn't "present"
            report["wrong_size"].append(_describe(document))
        elif checksums and _sha256(document.storage_path) != document.sha256:
            report["checksum_mismatch"].append(_describe(document))
    report["complete"] = not any(report[gap] for gap in GAPS)
    return report


def _describe(document):
    return {"kobo_id": document.submission.kobo_id, "uid": document.kobo_attachment_uid,
            "document_type": document.document_type, "status": document.status, "attempts": document.attempts,
            "error": document.error[:200], "path": document.storage_path}


def _sha256(path):
    digest = hashlib.sha256()
    with default_storage.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
