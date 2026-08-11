from msr_etl.services import UBRIndividualService, UBRLocationService


def run_ubr_individuals_import(reporter, **params):
    """Interim job body for the individuals import; wraps the existing service."""
    reporter.set_total(1)
    result = UBRIndividualService(reporter.job.user, **params).execute()
    _finish(reporter, result)


def run_ubr_locations_import(reporter, **params):
    """Interim job body for the locations import; wraps the existing service."""
    reporter.set_total(1)
    result = UBRLocationService(reporter.job.user, **params).execute()
    _finish(reporter, result)


def _finish(reporter, result):
    if result.get("success"):
        reporter.advance(1)
        reporter.succeed(result=result.get("data"))
    else:
        reporter.fail(result.get("detail") or result.get("message"))
