import logging

from django.apps import apps
from django.core.cache import cache
from django.utils.timezone import now
from django.utils.dateparse import parse_datetime
from core.models import User

from msr_etl.services.ubr_service import UBRLocationService
from msr_etl.models import UBRLocationInitialPullStatus


logger = logging.getLogger(__name__)

LOCATION_INITIAL_PULL_JOB_ID = "msr_etl_location_initial_pull"

JOB_STATUS_QUEUED = "QUEUED"
JOB_STATUS_PROCESSING = "PROCESSING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_FAILED = "FAILED"


def _status_cache_key(request_id: str) -> str:
    return f"msr_etl_location_initial_pull_status:{request_id}"


def _set_status(request_id: str, status: str, message: str = None, started_at: str = None, finished_at: str = None):
    started_dt = parse_datetime(started_at) if started_at else None
    finished_dt = parse_datetime(finished_at) if finished_at else None
    payload = {
        "request_id": request_id,
        "status": status,
        "message": message,
        "updated_at": now().isoformat(),
        "started_at": started_dt.isoformat() if started_dt else None,
        "finished_at": finished_dt.isoformat() if finished_dt else None,
    }
    # Persist status so it is visible across processes/workers even when cache is local-memory.
    UBRLocationInitialPullStatus.objects.update_or_create(
        request_id=request_id,
        defaults={
            "status": status,
            "message": message,
            "started_at": started_dt,
            "finished_at": finished_dt,
        },
    )
    # Keep status long enough for users to check after completion.
    cache.set(_status_cache_key(request_id), payload, timeout=60 * 60 * 24)
    return payload


def get_ubr_location_initial_pull_status(request_id: str):
    payload = cache.get(_status_cache_key(request_id))
    if payload:
        return payload

    stored = UBRLocationInitialPullStatus.objects.filter(request_id=request_id).first()
    if not stored:
        return None

    payload = {
        "request_id": stored.request_id,
        "status": stored.status,
        "message": stored.message,
        "updated_at": stored.updated_at.isoformat() if stored.updated_at else None,
        "started_at": stored.started_at.isoformat() if stored.started_at else None,
        "finished_at": stored.finished_at.isoformat() if stored.finished_at else None,
    }
    cache.set(_status_cache_key(request_id), payload, timeout=60 * 60 * 24)
    return payload


def run_ubr_location_initial_pull_job(user_id: int, request_id: str):
    logger.info("Initial pull job execution started (request_id=%s, user_id=%s)", request_id, user_id)
    started_at = now().isoformat()
    _set_status(request_id, JOB_STATUS_PROCESSING, "Background location pull is running", started_at=started_at)

    user = User.objects.filter(pk=user_id).first()
    if not user:
        message = f"Cannot run location initial pull: user {user_id} was not found"
        _set_status(
            request_id,
            JOB_STATUS_FAILED,
            message,
            started_at=started_at,
            finished_at=now().isoformat(),
        )
        raise RuntimeError(message)

    logger.info("Starting background job for full UBR location pull (user_id=%s)", user_id)
    try:
        result = UBRLocationService(user=user).execute()
        if not result.get("success"):
            raise RuntimeError(result.get("detail") or "Unknown ETL execution error")
        _set_status(
            request_id,
            JOB_STATUS_COMPLETED,
            "Background location pull completed successfully",
            started_at=started_at,
            finished_at=now().isoformat(),
        )
    except Exception as exc:
        logger.exception("Initial pull job execution failed (request_id=%s, user_id=%s): %s", request_id, user_id, exc)
        _set_status(
            request_id,
            JOB_STATUS_FAILED,
            str(exc),
            started_at=started_at,
            finished_at=now().isoformat(),
        )
        raise

    logger.info("Initial pull job execution completed successfully (request_id=%s, user_id=%s)", request_id, user_id)


def register_ubr_location_initial_pull_job(user_id: int, request_id: str) -> str:
    logger.info("Registering initial pull job (request_id=%s, user_id=%s)", request_id, user_id)
    scheduler_app = apps.get_app_config("apscheduler_runner")
    scheduler = getattr(scheduler_app, "scheduler", None)
    if scheduler is None:
        raise RuntimeError(
            "Scheduler is not running. Enable SCHEDULER_AUTOSTART to use background location initial pull."
        )

    existing_job = scheduler.get_job(LOCATION_INITIAL_PULL_JOB_ID)
    if existing_job:
        message = "A location initial pull job is already running or queued"
        logger.warning("Initial pull job registration rejected (request_id=%s): %s", request_id, message)
        _set_status(request_id, JOB_STATUS_FAILED, message)
        raise RuntimeError(message)

    _set_status(request_id, JOB_STATUS_QUEUED, "Background location pull has been queued")

    job = scheduler.add_job(
        run_ubr_location_initial_pull_job,
        trigger="date",
        id=LOCATION_INITIAL_PULL_JOB_ID,
        kwargs={"user_id": user_id, "request_id": request_id},
        replace_existing=False,
        misfire_grace_time=3600,
    )
    logger.info("Registered location initial pull background job (request_id=%s, job_id=%s)", request_id, job.id)
    return job.id
