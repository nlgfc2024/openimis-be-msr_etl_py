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
    """Crash recovery only. select_for_update(skip_locked=True) in
    sync_staged_units makes this safe to run alongside a still-live job."""
    _requeue_retryable_failed_units()
    _sync_orphaned_units()
    _close_completed_jobs()
    _fail_stale_jobs()


def _requeue_retryable_failed_units():
    max_attempts = int(MsrEtlConfig.sync_unit_max_attempts)
    MsrEtlSyncUnit.objects.filter(
        stage_status=MsrEtlSyncUnit.Status.STAGED,
        sync_status=MsrEtlSyncUnit.Status.FAILED,
        attempts__lt=max_attempts,
    ).update(sync_status=MsrEtlSyncUnit.Status.PENDING, updated_at=timezone.now())


def _sync_orphaned_units():
    """Reconstructs a ProgressReporter per job so retried units still
    advance processed/metrics, which _close_completed_jobs relies on."""
    job_uuids = (
        MsrEtlSyncUnit.objects
        .filter(stage_status=MsrEtlSyncUnit.Status.STAGED, sync_status=MsrEtlSyncUnit.Status.PENDING)
        .values_list("job_uuid", flat=True)
        .distinct()
    )
    for job_uuid in job_uuids:
        job = AsyncJob.objects.filter(id=job_uuid).first()
        if job is None:
            continue
        reporter = ProgressReporter(job) if job.total is not None else None
        sync_staged_units(job_uuid, reporter=reporter, user=job.user)


def _close_completed_jobs():
    """A targeted status update() only - never touches processed/metrics.
    processed >= total, not ==, since a retried unit advances twice."""
    open_jobs = AsyncJob.objects.filter(
        module="msr_etl",
    ).exclude(status__in=AsyncJob.TERMINAL_STATUSES).exclude(total__isnull=True)

    for job in open_jobs:
        if job.processed < job.total:
            continue
        fields = {"finished_at": timezone.now(), "updated_at": timezone.now()}
        if job_has_failed_units(job.id):
            fields["status"] = AsyncJob.Status.PARTIAL
            fields["error"] = "Some units failed; see msrEtlSyncUnits for details"
        else:
            fields["status"] = AsyncJob.Status.SUCCESS
        AsyncJob.objects.filter(id=job.id).exclude(status__in=AsyncJob.TERMINAL_STATUSES).update(**fields)


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
