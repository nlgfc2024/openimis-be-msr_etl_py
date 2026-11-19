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


def run_ubr_individuals_import(reporter, **params):
    source = UBRIndividualService(reporter.job.user, **params).source
    _run_staged_import(reporter, source, MsrEtlSyncUnit.Kind.INDIVIDUAL)


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
