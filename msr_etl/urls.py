from django.urls import path

from msr_etl.views import applicant_document, kobo_webhook, upload_import_file

urlpatterns = [
    path("upload/", upload_import_file),
    # The original form-number route, kept for clients that still post here
    path("form_number_upload/", upload_import_file),
    path("kobo_webhook/<str:source_type>/", kobo_webhook),
    path("individuals/<uuid:individual_id>/documents/<uuid:document_id>/", applicant_document),
]
