from msr_etl.staging import (
    enumerate_individual_units,
    enumerate_location_units,
    job_has_failed_units,
    stage_individual_unit,
    stage_location_unit,
    sync_staged_units,
)


def run_ubr_individuals_import(reporter, **params):
    user = reporter.job.user
    source, units = enumerate_individual_units(user, params)
    reporter.set_total(2 * len(units))
    for unit in units:
        staged = stage_individual_unit(reporter.job.id, source, unit)
        reporter.advance(**({"staged": 1} if staged else {"errors": 1}))
        reporter.message(f"Staged {unit['unit_code']}")

    sync_staged_units(reporter.job.id, reporter=reporter, user=user)
    _finish(reporter)


def run_ubr_locations_import(reporter, **params):
    user = reporter.job.user
    source, units = enumerate_location_units(user, params)
    reporter.set_total(2 * len(units))
    for unit in units:
        staged = stage_location_unit(reporter.job.id, source, unit)
        reporter.advance(**({"staged": 1} if staged else {"errors": 1}))
        reporter.message(f"Staged {unit['unit_type']} {unit['district']}")

    sync_staged_units(reporter.job.id, reporter=reporter, user=user)
    _finish(reporter)


def _finish(reporter):
    if job_has_failed_units(reporter.job.id):
        reporter.partial(error="Some units failed; see msrEtlSyncUnits for details")
    else:
        reporter.succeed()
