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
    units = source.enumerate_units()
    reporter.set_total(2 * len(units))
    for unit in units:
        staged = stage_unit(reporter.job.id, source, unit, kind)
        reporter.advance(**({"staged": 1} if staged else {"errors": 1}))
        reporter.message(f"Staged {unit['unit_type']} {unit['unit_code']}")

    sync_staged_units(reporter.job.id, reporter=reporter, user=reporter.job.user)
    _finish(reporter)


def _finish(reporter):
    # Retry inline so a normal run self-closes; the sweeper is then only
    # needed to recover a job whose worker process died mid-run.
    while has_retryable_failed_units(reporter.job.id):
        requeue_retryable_failed_units(reporter.job.id)
        sync_staged_units(reporter.job.id, reporter=reporter, user=reporter.job.user)

    if job_has_failed_units(reporter.job.id):
        reporter.partial(error="Some units failed; see msrEtlSyncUnits for details")
    else:
        reporter.succeed()
