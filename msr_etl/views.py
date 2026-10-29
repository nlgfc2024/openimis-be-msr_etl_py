import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from msr_etl.apps import MsrEtlConfig
from msr_etl.form_numbers import DEFAULT_FORM_NUMBER_COLUMN, FormNumberFileError, read_form_numbers
from msr_etl.models import MsrEtlUpload

logger = logging.getLogger(__name__)

FORM_NUMBER_CONNECTOR = "msr_form_numbers"
# How many rejected rows the response lists; the stored upload keeps more
_RESPONSE_REJECTED_ROWS = 20


class CanRunImports(IsAuthenticated):
    """The import (mutation) rights, read per request so a config change applies without a restart."""

    def has_permission(self, request, view):
        rights = MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms or []
        return super().has_permission(request, view) and request.user.has_perms(rights)


@api_view(["POST"])
@permission_classes([CanRunImports])
def upload_form_numbers(request):
    """Store the households to import for a form-number source (e.g. UPG's SCTP file).
    Returns the upload_id to start the import with, and what was read from the file."""
    source_type = request.data.get("source_type")
    upload_file = request.FILES.get("file")
    config = MsrEtlConfig.get_source_config(source_type) if source_type else {}
    if config.get("connector") != FORM_NUMBER_CONNECTOR:
        return Response({"success": False, "error": f"Source '{source_type}' does not import from a file of form numbers"},
                        status=status.HTTP_400_BAD_REQUEST)
    if not upload_file:
        return Response({"success": False, "error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = read_form_numbers(upload_file, config.get("form_number_column") or DEFAULT_FORM_NUMBER_COLUMN)
    except FormNumberFileError as exc:
        return Response({"success": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

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
