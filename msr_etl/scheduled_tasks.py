import logging
from datetime import timedelta

from apscheduler.triggers.interval import IntervalTrigger
from django.utils import timezone

from core.models import AsyncJob
from core.services import ProgressReporter
from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.staging import job_has_failed_units, sync_staged_units

logger = logging.getLogger(__name__)

# PARTIAL is deliberately not here: SUCCESS/FAILED/CANCELLED are final - never re-swept, regardless
_NEVER_RESWEEP_STATUSES = (AsyncJob.Status.SUCCESS, AsyncJob.Status.FAILED, AsyncJob.Status.CANCELLED)


def schedule_tasks(scheduler):
    sweep_minutes = max(int(MsrEtlConfig.sync_sweep_interval_minutes), 1)
    scheduler.add_job(
        sweep_sync_units,
        trigger=IntervalTrigger(minutes=sweep_minutes),
        id="msr_etl_sweep_sync_units",
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Scheduled msr_etl sync unit sweeper every %s minutes", sweep_minutes)

    scheduler.add_job(
        cleanup_staged_payloads,
        trigger=IntervalTrigger(hours=1),
        id="msr_etl_cleanup_staged_payloads",
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Scheduled msr_etl staged payload cleanup hourly")


def sweep_sync_units():
    """Crash recovery only. A job that's merely still staging looks
    identical to a crashed one by unit status alone, so eligibility is
    gated on the job itself going idle - see _stale_job_uuids."""
    stale_job_uuids = _stale_job_uuids()
    _requeue_retryable_failed_units(stale_job_uuids)
    _sync_orphaned_units(stale_job_uuids)
    _close_completed_jobs()
    _fail_stale_jobs()


def _stale_job_uuids():
    """A live run's own reporter keeps updated_at fresh on every unit, so
    only jobs idle past sync_orphan_grace_minutes are genuinely orphaned -
    without this, sweeping a still-running job spins up a second
    ProgressReporter and the two writers race on processed/metrics.

    CANCELLED/SUCCESS/FAILED jobs are excluded regardless of idle time: a
    cancelled job's remaining pending units must stay untouched, not get
    synced anyway once the grace period passes. Only PARTIAL is left
    eligible, since its failed units are the ones sweeping is meant to
    retry - see _close_completed_jobs for the matching re-close half."""
    grace_minutes = int(MsrEtlConfig.sync_orphan_grace_minutes)
    stale_cutoff = timezone.now() - timedelta(minutes=grace_minutes)
    candidate_job_uuids = list(
        MsrEtlSyncUnit.objects
        .filter(
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status__in=[MsrEtlSyncUnit.Status.PENDING, MsrEtlSyncUnit.Status.FAILED],
        )
        .values_list("job_uuid", flat=True)
        .distinct()
    )
    if not candidate_job_uuids:
        return set()
    return set(
        AsyncJob.objects.filter(id__in=candidate_job_uuids, updated_at__lt=stale_cutoff)
        .exclude(status__in=_NEVER_RESWEEP_STATUSES)
        .values_list("id", flat=True)
    )


def _requeue_retryable_failed_units(stale_job_uuids):
    if not stale_job_uuids:
        return
    max_attempts = int(MsrEtlConfig.sync_unit_max_attempts)
    MsrEtlSyncUnit.objects.filter(
        job_uuid__in=stale_job_uuids,
        stage_status=MsrEtlSyncUnit.Status.STAGED,
        sync_status=MsrEtlSyncUnit.Status.FAILED,
        attempts__lt=max_attempts,
    ).update(sync_status=MsrEtlSyncUnit.Status.PENDING, updated_at=timezone.now())


def _sync_orphaned_units(stale_job_uuids):
    """Reconstructs a ProgressReporter per job so retried units still
    advance processed/metrics, which _close_completed_jobs relies on."""
    for job_uuid in stale_job_uuids:
        job = AsyncJob.objects.filter(id=job_uuid).first()
        if job is None:
            continue
        reporter = ProgressReporter(job) if job.total is not None else None
        sync_staged_units(job_uuid, reporter=reporter, user=job.user)


def _close_completed_jobs():
    """A targeted status update() only - never touches processed/metrics.
    processed >= total, not ==, since a retried unit advances twice.

    PARTIAL is reconsidered here (unlike the other terminal statuses) so a
    job whose sweeper-retried units all end up succeeding is promoted to
    SUCCESS instead of being stuck at PARTIAL forever - the counterpart to
    _stale_job_uuids leaving PARTIAL jobs eligible for the sweep."""
    open_jobs = AsyncJob.objects.filter(
        module="msr_etl",
    ).exclude(status__in=_NEVER_RESWEEP_STATUSES).exclude(total__isnull=True)

    for job in open_jobs:
        if job.processed < job.total:
            continue
        fields = {"finished_at": timezone.now(), "updated_at": timezone.now()}
        if job_has_failed_units(job.id):
            fields["status"] = AsyncJob.Status.PARTIAL
            fields["error"] = "Some units failed; see msrEtlSyncUnits for details"
        else:
            fields["status"] = AsyncJob.Status.SUCCESS
        AsyncJob.objects.filter(id=job.id).exclude(status__in=_NEVER_RESWEEP_STATUSES).update(**fields)


def _fail_stale_jobs():
    stale_hours = int(MsrEtlConfig.job_stale_after_hours)
    cutoff = timezone.now() - timedelta(hours=stale_hours)
    AsyncJob.objects.filter(module="msr_etl", created_at__lt=cutoff).exclude(
        status__in=AsyncJob.TERMINAL_STATUSES
    ).update(
        status=AsyncJob.Status.FAILED,
        error="Job exceeded job_stale_after_hours without completing",
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )


def cleanup_staged_payloads():
    """FAILED units keep raw_payload until resolved - retrying must not
    re-pay the UBR fetch."""
    retention_hours = int(MsrEtlConfig.staging_retention_hours)
    cutoff = timezone.now() - timedelta(hours=retention_hours)
    updated = MsrEtlSyncUnit.objects.filter(
        sync_status=MsrEtlSyncUnit.Status.SYNCED,
        raw_payload__isnull=False,
        updated_at__lt=cutoff,
    ).update(raw_payload=None, updated_at=timezone.now())
    if updated:
        logger.info("Purged raw_payload for %s synced sync units past retention", updated)
