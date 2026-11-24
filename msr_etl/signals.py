import logging

from django.dispatch import Signal

logger = logging.getLogger(__name__)

# Sent once a Kobo application has created (IMPORTED) or updated (UPDATED) its Individual, after the
# transaction commits. Arguments: submission (KoboSubmission), individual, status. Held or refused
# applications don't send it.
kobo_submission_applied = Signal()

# Sent once an applicant's document has been downloaded and stored (status STORED), after the transaction
# commits. Arguments: document (ApplicantDocument). Documents are downloaded after their application is
# imported, so this is how a module learns a file it reads is now there.
applicant_document_stored = Signal()


def send(signal, sender, **kwargs):
    """Send ``signal`` to every receiver; a receiver that raises is logged, so it can't interrupt msr_etl's work."""
    for receiver, outcome in signal.send_robust(sender=sender, **kwargs):
        if isinstance(outcome, Exception):
            logger.error("Receiver %s of %s failed for %s", receiver, sender.__name__, kwargs,
                         exc_info=outcome)
