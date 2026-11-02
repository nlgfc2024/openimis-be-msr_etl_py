from django.urls import path

from msr_etl.views import upload_form_numbers

urlpatterns = [
    path("form_number_upload/", upload_form_numbers),
]
