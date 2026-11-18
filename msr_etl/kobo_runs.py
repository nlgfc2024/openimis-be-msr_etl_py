import hmac
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import AsyncJob, User
from core.services import run_as_scheduled_job

from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrEtlSourceRun
from msr_etl.source_registry import get_connector, list_individual_source_types

logger = logging.getLogger(__name__)

KOBO_CONNECTOR = "kobo"
IMPORT_TASK = "msr_etl.jobs.run_ubr_individuals_import"
IMPORT_JOB_TYPE = "ubr_individuals_import"
# A claim older than this is from a starter that died before recording its job
CLAIM_TIMEOUT = timedelta(minutes=5)


class ImportAlreadyRunning(Exception):
    pass


def kobo_source_types():
    return [source_type for source_type in list_individual_source_types()
            if get_connector(source_type) == KOBO_CONNECTOR]


def start_import(source_type, user=None, client_mutation_id=None):
    """Start an import job for a source unless one is already queued or running; returns the job uuid.
    Raises ImportAlreadyRunning otherwise."""
    return _start_exclusive(source_type, IMPORT_TASK, IMPORT_JOB_TYPE, {"source_type": source_type},
                            user=user, client_mutation_id=client_mutation_id)


def _start_exclusive(lock_key, task, job_type, params, user=None, client_mutation_id=None):
    user = user or _import_user()
    _claim(lock_key)
    try:
        # Outside the lock's transaction: the scheduler may run the job at once, so it must be committed
        job_uuid = run_as_scheduled_job(task, module="msr_etl", job_type=job_type, user=user, params=params,
                                        client_mutation_id=client_mutation_id)
    except Exception:
        MsrEtlSourceRun.objects.filter(source_type=lock_key).update(claimed_at=None)
        raise
    MsrEtlSourceRun.objects.filter(source_type=lock_key).update(job_uuid=job_uuid, claimed_at=None)
    return job_uuid


def _claim(lock_key):
    for _ in range(2):
        try:
            with transaction.atomic():
                run, _ = MsrEtlSourceRun.objects.select_for_update().get_or_create(source_type=lock_key)
                if run.claimed_at and run.claimed_at > timezone.now() - CLAIM_TIMEOUT:
                    raise ImportAlreadyRunning(f"A '{lock_key}' job is being started")
                if run.job_uuid and AsyncJob.objects.filter(id=run.job_uuid).exclude(
                        status__in=AsyncJob.TERMINAL_STATUSES).exists():
                    raise ImportAlreadyRunning(f"A '{lock_key}' job is already running")
                run.claimed_at = timezone.now()
                run.save()
                return
        except IntegrityError:
            # Another caller created the row first; lock it this time
            continue
    raise ImportAlreadyRunning(f"Could not start a '{lock_key}' job")


def start_scheduled_kobo_imports():
    """Scheduler entry point: a run for every Kobo source that has nothing running."""
    for source_type in kobo_source_types():
        if MsrEtlConfig.get_source_config(source_type).get("scheduled", True) is False:
            continue
        try:
            start_import(source_type)
        except ImportAlreadyRunning:
            logger.debug("Kobo import from '%s' still running; not starting another", source_type)
        except Exception:
            logger.exception("Could not start the scheduled Kobo import from '%s'", source_type)


def webhook_secret_matches(source_type, given):
    secret = str(MsrEtlConfig.get_source_config(source_type).get("webhook_secret") or "")
    # No secret configured means the webhook is off
    return bool(secret) and hmac.compare_digest(secret, str(given or ""))


def _import_user():
    username = MsrEtlConfig.sink_import_username
    user = User.objects.filter(username=username).first() if username else None
    if user is None:
        raise ValueError("Scheduled and webhook imports run as sink_import_username, which is not set or not found")
    return user
