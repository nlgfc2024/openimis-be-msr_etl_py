from django.dispatch import Signal

# Sent once a Kobo application has created (IMPORTED) or updated (UPDATED) its Individual, after the
# transaction commits. Arguments: submission (KoboSubmission), individual, status. Held or refused
# applications don't send it.
kobo_submission_applied = Signal()
