"""
Reviewing Kobo submissions: outcome counts, and for a held one, its answers next to the record it was held from.
Seen only with the kobo_submission_perms right; a submission linked to an individual follows that individual's
location limits.
"""
import re

from django.db.models import Count, Q

from msr_etl.apps import MsrEtlConfig
from msr_etl.models import ApplicantDocument, KoboSubmission

# A repeat application updates the existing record only if these all match; otherwise it is held
DEFAULT_MATCH_FIELDS = ("first_name", "last_name", "dob", "sex")
NAME_FIELDS = ("first_name", "last_name")


def same(value):
    """Compared ignoring case and extra spaces."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip().lower()


def match_fields(source_type):
    return tuple(MsrEtlConfig.get_source_config(source_type).get("match_fields") or DEFAULT_MATCH_FIELDS)


def compare(individual, record, fields):
    """[{"field", "mis", "applied", "differs"}] for each match field, the MIS value as the record holds it now."""
    native = {field.name for field in type(individual)._meta.get_fields()}
    rows = []
    for field in fields:
        current = getattr(individual, field) if field in native else (individual.json_ext or {}).get(field)
        applied = record.get(field)
        rows.append({"field": field, "mis": "" if current is None else str(current),
                     "applied": "" if applied is None else str(applied), "differs": same(current) != same(applied)})
    return rows


def answers(submission):
    """The submission's answers by field name, as the adapter reads them."""
    from msr_etl.adapters.kobo_adapter import KoboIndividualAdapter

    return KoboIndividualAdapter._answers(submission.data or {})


def comparison(submission):
    """A held submission's match fields next to its individual's; None when it isn't held from one."""
    if submission.status != KoboSubmission.Status.HELD or submission.individual is None:
        return None
    return compare(submission.individual, answers(submission), match_fields(submission.source_type))


def visible_submissions(user, source_types):
    """The submissions of these sources the user may see: those of individuals in their locations, and those
    linked to none (no national ID, invalid)."""
    from individual.models import Individual

    visible = Individual.get_queryset(None, user).values("id")
    return KoboSubmission.objects.filter(source_type__in=source_types).filter(
        Q(individual__isnull=True) | Q(individual_id__in=visible))


def search(queryset, text):
    """By national ID (any part) or Kobo submission id."""
    text = str(text or "").strip()
    if not text:
        return queryset
    condition = Q(national_id__icontains=re.sub(r"\s+", "", text))
    if text.isdigit():
        condition |= Q(kobo_id=int(text))
    return queryset.filter(condition)


def summary(queryset):
    """{"submissions": {status: count}, "documents": {status: count}} over these submissions."""
    statuses = dict(queryset.order_by().values_list("status").annotate(n=Count("id")))
    documents = dict(ApplicantDocument.objects.filter(submission__in=queryset.values("id")).order_by()
                     .values_list("status").annotate(n=Count("id")))
    return {"submissions": {status: statuses.get(status, 0) for status in KoboSubmission.Status.values},
            "documents": {status: documents.get(status, 0) for status in ApplicantDocument.Status.values}}
