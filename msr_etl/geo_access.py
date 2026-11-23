from django.core.exceptions import PermissionDenied, ValidationError

def require_district_scope(user, source_instance, district_id, policy="msr_etl"):
    # A queued worker must not retain revoked host roles or district assignments.
    if user is not None:
        from core.models import User
        if isinstance(user, User):
            from django.core.cache import cache
            user.refresh_from_db()
            if user.i_user_id:
                cache.delete("rights_" + str(user.i_user_id))
    from msr_etl.apps import MsrEtlConfig
    rights = MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms
    if (user is None or user.is_anonymous or not getattr(user, "is_active", True) or not rights
            or not all(user.has_perms([right]) for right in rights)):
        raise PermissionDenied("MSR_GEO_SYNC_ACCESS_DENIED")
    # Reuse CoreMIS's geographic assignment policy rather than maintaining another filter.
    from location.models import allowed_micro_catchment_district_ids, MsrLocationReference
    allowed = allowed_micro_catchment_district_ids(user)
    if allowed is not None and not MsrLocationReference.objects.filter(
        source_instance=source_instance, msr_type="districts", msr_id=district_id,
        location_id__in=allowed, location__validity_to__isnull=True,
    ).exists():
        raise PermissionDenied("MSR_GEO_SCOPE_DENIED")


def resolve_district(user, source_instance, district_id=None, district_uuid=None):
    if district_uuid:
        from location.models import Location, MsrLocationReference
        local = Location.get_queryset(Location.objects.filter(type="R", validity_to__isnull=True), user).filter(uuid=district_uuid).first()
        if local is None:
            raise PermissionDenied("MSR_GEO_SCOPE_DENIED")
        matches = list(MsrLocationReference.objects.filter(source_instance=source_instance, msr_type="districts",
                                                          location=local).values_list("msr_id", flat=True)[:2])
        if len(matches) != 1:
            raise ValidationError("MSR_GEO_DISTRICT_LINK_REQUIRED")
        if district_id is not None and str(district_id) != str(matches[0]):
            raise ValidationError("MSR_GEO_DISTRICT_ID_MISMATCH")
        district_id = matches[0]
    if isinstance(district_id, bool) or not str(district_id or "").isdigit() or int(district_id) <= 0:
        raise ValidationError("MSR_GEO_DISTRICT_REQUIRED")
    return int(district_id)
