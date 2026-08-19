import logging
from datetime import timedelta

from apscheduler.triggers.interval import IntervalTrigger
from django.utils import timezone

from core.models import AsyncJob
from core.services import ProgressReporter
from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.staging import (
    has_retryable_failed_units,
    job_has_failed_units,
    requeue_retryable_failed_units,
    sync_staged_units,
)

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
    # Crash recovery only - a live run now retries and closes itself
    # (jobs.py's _finish), so this only matters if the worker died mid-run.
    stale_job_uuids = _stale_job_uuids()
    for job_uuid in stale_job_uuids:
        requeue_retryable_failed_units(job_uuid)
    _sync_orphaned_units(stale_job_uuids)
    _close_completed_jobs()
    _fail_stale_jobs()


def _stale_job_uuids():
    # updated_at stays fresh while a job's own reporter is writing, so only
    # truly idle jobs are swept - otherwise two ProgressReporters could race
    # on the same job's processed/metrics.
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
        .exclude(status__in=AsyncJob.TERMINAL_STATUSES)
        .values_list("id", flat=True)
    )


def _sync_orphaned_units(stale_job_uuids):
    for job_uuid in stale_job_uuids:
        job = AsyncJob.objects.filter(id=job_uuid).first()
        if job is None:
            continue
        reporter = ProgressReporter(job) if job.total is not None else None
        sync_staged_units(job_uuid, reporter=reporter, user=job.user)


def _close_completed_jobs():
    # Backstop for a job whose worker died before self-closing; jobs that
    # completed normally are already terminal by the time this runs.
    open_jobs = AsyncJob.objects.filter(
        module="msr_etl",
    ).exclude(status__in=AsyncJob.TERMINAL_STATUSES).exclude(total__isnull=True)

    for job in open_jobs:
        if job.processed < job.total:
            continue
        if has_retryable_failed_units(job.id):
            continue
        fields = {"finished_at": timezone.now(), "updated_at": timezone.now()}
        if job_has_failed_units(job.id):
            fields["status"] = AsyncJob.Status.PARTIAL
            fields["error"] = "Some units failed; see msrEtlSyncUnits for details"
        else:
            fields["status"] = AsyncJob.Status.SUCCESS
        # Re-excluded here in case the job went terminal between the read above and this write.
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
    retention_hours = int(MsrEtlConfig.staging_retention_hours)
    cutoff = timezone.now() - timedelta(hours=retention_hours)
    updated = MsrEtlSyncUnit.objects.filter(
        sync_status=MsrEtlSyncUnit.Status.SYNCED,
        raw_payload__isnull=False,
        updated_at__lt=cutoff,
    ).update(raw_payload=None, updated_at=timezone.now())
    if updated:
        logger.info("Purged raw_payload for %s synced sync units past retention", updated)
