from django.urls import path

from msr_etl.views import upload_import_file

urlpatterns = [
    path("upload/", upload_import_file),
    # The original form-number route, kept for clients that still post here
    path("form_number_upload/", upload_import_file),
]
