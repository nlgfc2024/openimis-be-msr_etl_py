import logging

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from msr_etl import kobo_runs
from msr_etl.apps import MsrEtlConfig
from msr_etl.form_numbers import DEFAULT_FORM_NUMBER_COLUMN, FormNumberFileError, read_form_numbers
from msr_etl.location_file import LocationFileError, read_location_file, resolve_location_codes
from msr_etl.models import MsrEtlUpload
from msr_etl.source_registry import source_enabled

logger = logging.getLogger(__name__)

FORM_NUMBER_CONNECTOR = "msr_form_numbers"
LOCATION_FILE_CONNECTOR = "excel_locations"
# How many rejected rows the response lists; the stored upload keeps more
_RESPONSE_REJECTED_ROWS = 20


class CanRunImports(IsAuthenticated):
    """The import (mutation) rights, read per request so a config change applies without a restart."""

    def has_permission(self, request, view):
        rights = MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms or []
        # has_perms([]) is True, so no configured right means no access
        return bool(rights) and super().has_permission(request, view) and request.user.has_perms(rights)


@api_view(["POST"])
@permission_classes([CanRunImports])
def upload_import_file(request):
    """Store an import file for a file-based source: form numbers (e.g. UPG's SCTP file) or a location list.
    Returns the upload_id to start the import with, and what was read from the file."""
    source_type = request.data.get("source_type")
    upload_file = request.FILES.get("file")
    config = MsrEtlConfig.get_source_config(source_type) if source_type else {}
    handler = _UPLOAD_HANDLERS.get(config.get("connector"))
    if handler and not source_enabled(source_type):
        return Response({"success": False, "error": f"Source '{source_type}' is turned off"},
                        status=status.HTTP_400_BAD_REQUEST)
    if not handler:
        return Response({"success": False, "error": f"Source '{source_type}' does not import from a file"},
                        status=status.HTTP_400_BAD_REQUEST)
    if not upload_file:
        return Response({"success": False, "error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
    return handler(request, source_type, upload_file, config)


def _rejected_response(exc):
    return Response({"success": False, "error": str(exc), "rejected": exc.rejected_count,
                     "rejected_rows": exc.rejected_rows[:_RESPONSE_REJECTED_ROWS]},
                    status=status.HTTP_400_BAD_REQUEST)


def _store_locations(request, source_type, upload_file, config):
    levels = config.get("location_levels")
    try:
        result = read_location_file(upload_file, levels, config.get("city_codes"))
        # What the import would do to the MIS as it is now; the job resolves the codes again when it runs
        preview = resolve_location_codes(result["places"], levels)
    except LocationFileError as exc:
        return _rejected_response(exc)

    upload = MsrEtlUpload.objects.create(
        source_type=source_type,
        file_name=upload_file.name,
        locations=result["places"],
        rejected_rows=result["rejected_rows"] + preview["conflicts"],
        uploaded_by=request.user.username,
    )
    logger.info("Stored %s places for source '%s' (upload %s)", len(result["places"]), source_type, upload.id)
    rejected_rows = result["rejected_rows"] + preview["conflicts"]
    return Response({
        "success": True,
        "upload_id": str(upload.id),
        "records": len(preview["locations"]),
        "level_counts": result["level_counts"],
        "ignored_columns": result["ignored_columns"],
        "rows": result["row_count"],
        "rejected": result["rejected_count"] + len(preview["conflicts"]),
        "rejected_rows": rejected_rows[:_RESPONSE_REJECTED_ROWS],
        "generated_codes": preview["generated_codes"],
        **{key: preview[key][:_RESPONSE_REJECTED_ROWS] for key in ("code_changes", "renamed", "moved")},
        **{key: preview[key] for key in ("code_changes_count", "renamed_count", "moved_count")},
    })


def _store_form_numbers(request, source_type, upload_file, config):
    try:
        result = read_form_numbers(upload_file, config.get("form_number_column") or DEFAULT_FORM_NUMBER_COLUMN)
    except FormNumberFileError as exc:
        return _rejected_response(exc)

    upload = MsrEtlUpload.objects.create(
        source_type=source_type,
        file_name=upload_file.name,
        form_numbers=result["form_numbers"],
        rejected_rows=result["rejected_rows"],
        uploaded_by=request.user.username,
    )
    logger.info("Stored %s form numbers for source '%s' (upload %s)", len(result["form_numbers"]), source_type, upload.id)
    return Response({
        "success": True,
        "upload_id": str(upload.id),
        "households": len(result["form_numbers"]),
        "rows": result["row_count"],
        "rejected": result["rejected_count"],
        "duplicates": result["duplicate_count"],
        "rejected_rows": result["rejected_rows"][:_RESPONSE_REJECTED_ROWS],
    })


_UPLOAD_HANDLERS = {
    FORM_NUMBER_CONNECTOR: _store_form_numbers,
    LOCATION_FILE_CONNECTOR: _store_locations,
}


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def kobo_webhook(request, source_type):
    """Kobo's REST service calls this when a submission arrives; it only starts an import of what is new.
    The source's webhook_secret must come as the X-Webhook-Secret header (or ?secret=)."""
    given = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret")
    if source_type not in kobo_runs.kobo_source_types() or not kobo_runs.webhook_secret_matches(source_type, given):
        # The same answer for an unknown source and a wrong secret
        return Response({"success": False}, status=status.HTTP_403_FORBIDDEN)
    try:
        job_uuid = kobo_runs.start_import(source_type)
    except kobo_runs.ImportAlreadyRunning:
        # The running import, or the next one, picks the submission up
        return Response({"success": True, "started": False}, status=status.HTTP_202_ACCEPTED)
    return Response({"success": True, "started": True, "job_uuid": str(job_uuid)}, status=status.HTTP_202_ACCEPTED)
