from msr_etl.models import MsrEtlSyncUnit
from msr_etl.services import UBRIndividualService, UBRLocationService
from msr_etl.staging import (
    has_retryable_failed_units,
    job_has_failed_units,
    requeue_retryable_failed_units,
    stage_unit,
    sync_staged_units,
)


def run_ubr_individuals_import(reporter, **params):
    source = UBRIndividualService(reporter.job.user, **params).source
    _run_staged_import(reporter, source, MsrEtlSyncUnit.Kind.INDIVIDUAL)


def run_ubr_locations_import(reporter, **params):
    source = UBRLocationService(reporter.job.user, **params).source
    _run_staged_import(reporter, source, MsrEtlSyncUnit.Kind.LOCATION)


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
