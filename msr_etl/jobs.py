import logging

from msr_etl.models import MsrEtlSyncUnit
from msr_etl.services import UBRIndividualService, UBRLocationService
from msr_etl.source_registry import get_connector
from msr_etl.staging import (
    has_retryable_failed_units,
    job_has_failed_units,
    requeue_retryable_failed_units,
    stage_unit,
    sync_staged_units,
)

LOCATION_FILE_CONNECTOR = "excel_locations"
KOBO_CONNECTOR = "kobo"

logger = logging.getLogger(__name__)


def run_ubr_individuals_import(reporter, **params):
    source = UBRIndividualService(reporter.job.user, **params).source
    _run_staged_import(reporter, source, MsrEtlSyncUnit.Kind.INDIVIDUAL)
    if get_connector(source.source_type) == KOBO_CONNECTOR:
        # Their files follow at once, in their own job, so a slow download never holds the import
        from msr_etl.kobo_runs import start_documents_downloads

        start_documents_downloads([source.source_type], user=reporter.job.user)


def run_kobo_documents_download(reporter, source_type):
    """Copy the applicant documents not stored yet from Kobo into the MIS file storage."""
    from msr_etl.kobo_documents import download_document, record_failure, to_download
    from msr_etl.sources import KoboSubmissionSource

    documents = list(to_download(source_type))
    reporter.set_total(len(documents))
    reporter.message("Copying applicant documents from Kobo...")
    kobo, failed = KoboSubmissionSource(source_type), 0
    for document in documents:
        try:
            download_document(kobo, document)
            reporter.advance(k=1, stored=1)
        except Exception as exc:
            logger.warning("Could not store document %s (%s): %s", document.id, document.document_type, exc)
            record_failure(document, exc)
            failed += 1
            reporter.advance(k=1, errors=1)
    reporter.message(None)
    if failed:
        reporter.partial(error=f"{failed} documents could not be copied; they are retried on the next runs")
    else:
        reporter.succeed()


def run_ubr_locations_import(reporter, **params):
    source = UBRLocationService(reporter.job.user, **params).source
    _run_staged_import(reporter, source, MsrEtlSyncUnit.Kind.LOCATION)
    if get_connector(source.source_type) == LOCATION_FILE_CONNECTOR:
        # The MIS is the source of locations: Kobo forms get the new lists straight away
        from msr_etl.kobo_runs import start_location_choices_pushes

        start_location_choices_pushes(user=reporter.job.user)


def run_kobo_location_choices_push(reporter, source_type):
    """Put the MIS location lists into a Kobo source's form and redeploy it."""
    from msr_etl.kobo_location_choices import push_location_choices

    reporter.set_total(1)
    reporter.message("Updating the Kobo form's location lists...")
    result = push_location_choices(source_type)
    reporter.advance(k=1, files_updated=len(result["updated"]), redeployed=int(result["redeployed"]))
    reporter.message(None)
    if result["warning"]:
        reporter.partial(error=result["warning"])
    else:
        reporter.succeed()


def _run_staged_import(reporter, source, kind):
    # Progress is in records (individuals/locations), so the total is only
    # known once everything is downloaded; a failed download has no count.
    units = source.enumerate_units()
    reporter.message("Downloading data...")
    total = 0
    for unit in units:
        record_count = stage_unit(reporter.job.id, source, unit, kind)
        if record_count is None:
            reporter.advance(k=0, download_errors=1)
        else:
            total += record_count
            reporter.advance(k=0, staged=record_count)

    reporter.set_total(total)
    reporter.message("Importing data...")
    sync_staged_units(reporter.job.id, reporter=reporter, user=reporter.job.user)
    _finish(reporter)


def _finish(reporter):
    # Retry inline so a normal run self-closes; the sweeper is then only
    # needed to recover a job whose worker process died mid-run.
    while has_retryable_failed_units(reporter.job.id):
        requeue_retryable_failed_units(reporter.job.id)
        sync_staged_units(reporter.job.id, reporter=reporter, user=reporter.job.user)

    # The status chip says how it ended; a leftover "Importing data..." would contradict it
    reporter.message(None)
    if job_has_failed_units(reporter.job.id):
        reporter.partial(error="Some records failed to download or import; see the sync log for details")
    else:
        reporter.succeed()
