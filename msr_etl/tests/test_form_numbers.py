import io
import uuid
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.test_helpers import LogInHelper
from msr_etl.apps import MsrEtlConfig
from msr_etl.form_numbers import FormNumberFileError, read_form_numbers
from msr_etl.models import MsrEtlSyncUnit, MsrEtlUpload
from msr_etl.services.ubr_service import UBRIndividualService
from msr_etl.source_registry import resolve_individual_source
from msr_etl.sources import MsrFormNumberSource
from msr_etl.views import upload_form_numbers

SCTP_CONFIG = {"connector": "msr_form_numbers", "kind": "individual", "form_numbers_per_unit": 2}


def _csv(text, name="sctp.csv"):
    return SimpleUploadedFile(name, text.encode())


def _xlsx(df, name="sctp.xlsx"):
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    return SimpleUploadedFile(name, buffer.getvalue())


class ReadFormNumbersTestCase(SimpleTestCase):

    def test_reads_the_form_number_column(self):
        result = read_form_numbers(_csv("form_number,other\n30874398,a\n30874399,b\n"))

        self.assertEqual(result["form_numbers"], ["30874398", "30874399"])
        self.assertEqual(result["row_count"], 2)

    def test_matches_the_header_loosely_and_reads_excel_numbers(self):
        result = read_form_numbers(_xlsx(pd.DataFrame({"Form Number": [30874398.0, 30874399]})))

        self.assertEqual(result["form_numbers"], ["30874398", "30874399"])

    def test_a_single_column_file_is_used_whatever_its_header(self):
        self.assertEqual(read_form_numbers(_csv("HH\n30874398\n"))["form_numbers"], ["30874398"])

    def test_skips_blanks_keeps_duplicates_once_and_rejects_non_numbers(self):
        result = read_form_numbers(_csv("form_number\n30874398\n\n30874398\nABC1\n30874399\n"))

        self.assertEqual(result["form_numbers"], ["30874398", "30874399"])
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["rejected_rows"], [{"row": 5, "value": "ABC1", "reason": "not a number"}])

    def test_reports_a_missing_column(self):
        with self.assertRaisesRegex(FormNumberFileError, "No 'form_number' column; the file has: a, b"):
            read_form_numbers(_csv("a,b\n1,2\n"))

    def test_rejects_files_without_usable_form_numbers_or_of_other_types(self):
        with self.assertRaisesRegex(FormNumberFileError, "no usable form numbers"):
            read_form_numbers(_csv("form_number\nABC\n"))
        with self.assertRaisesRegex(FormNumberFileError, "Unsupported file type"):
            read_form_numbers(_csv("form_number\n1\n", name="sctp.txt"))


@patch.object(MsrEtlConfig, "sources", {"sctp": SCTP_CONFIG})
class MsrFormNumberSourceTestCase(TestCase):

    def setUp(self):
        self.upload = MsrEtlUpload.objects.create(source_type="sctp", file_name="sctp.xlsx",
                                                  form_numbers=["101", "102", "103"])

    def _source(self, **kwargs):
        return MsrFormNumberSource("sctp", upload_id=self.upload.id, auth_provider=MagicMock(), **kwargs)

    def test_is_registered_with_the_msr_adapter(self):
        source_cls, adapter_cls = resolve_individual_source("sctp")
        self.assertIs(source_cls, MsrFormNumberSource)
        self.assertEqual(adapter_cls.__name__, "UBRIndividualAdapter")

    def test_units_are_batches_of_form_numbers(self):
        units = self._source().enumerate_units()

        self.assertEqual([(u["unit_code"], u["form_numbers"]) for u in units], [("1-2", ["101", "102"]), ("3-3", ["103"])])
        self.assertEqual(units[0]["unit_type"], MsrEtlSyncUnit.UnitType.FORM_NUMBERS)

    @patch("msr_etl.sources.msr_form_number_source.create_retry_session")
    def test_fetches_each_household_with_the_full_percentile_range(self, _session):
        source = self._source()
        with patch.object(source, "_post_households", side_effect=[
            [{"form_number": "101", "household_members": [{}, {}]}],
            [],  # not in MSR
        ]) as post, patch.object(source, "_get_headers", return_value={}):
            rows = source.fetch_unit({"form_numbers": ["101", "102"]})

        self.assertEqual([row["form_number"] for row in rows], ["101"])
        self.assertEqual(post.call_args_list[0][0][3],
                         {"form_number": "101", "lower_percentile_category": "0", "upper_percentile_category": "100"})
        self.assertEqual(source.count_records(rows), 2)

    @patch("msr_etl.sources.msr_form_number_source.create_retry_session")
    def test_keeps_only_the_requested_household(self, _session):
        source = self._source()
        with patch.object(source, "_post_households", return_value=[{"form_number": "999"}]), \
                patch.object(source, "_get_headers", return_value={}):
            self.assertEqual(source.fetch_unit({"form_numbers": ["101"]}), [])

    def test_requires_an_upload_of_its_own_source(self):
        with self.assertRaisesRegex(MsrFormNumberSource.Error, "upload_id is required"):
            MsrFormNumberSource("sctp", auth_provider=MagicMock())
        other = MsrEtlUpload.objects.create(source_type="pwp", file_name="x.csv", form_numbers=["1"])
        with self.assertRaisesRegex(MsrFormNumberSource.Error, "not found for source 'sctp'"):
            MsrFormNumberSource("sctp", upload_id=other.id, auth_provider=MagicMock())

    @patch("msr_etl.sources.ubr_source.get_auth_provider")
    def test_service_takes_an_upload_instead_of_a_district(self, _auth):
        service = UBRIndividualService(MagicMock(), source_type="sctp", upload_id=str(self.upload.id),
                                       adapter=MagicMock(), sink=MagicMock())

        self.assertIsInstance(service.source, MsrFormNumberSource)
        self.assertEqual(service.source.form_numbers, ["101", "102", "103"])

    def test_area_pulls_still_need_a_district(self):
        with self.assertRaisesRegex(ValueError, "district is required"):
            UBRIndividualService(MagicMock(), source_type="ubr", adapter=MagicMock(), sink=MagicMock())


@patch.object(MsrEtlConfig, "sources", {"sctp": SCTP_CONFIG, "pwp": {"connector": "openimis_gql"}})
class UploadFormNumbersViewTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()
        self.factory = APIRequestFactory()

    def _post(self, data, user=None):
        request = self.factory.post("/api/msr_etl/form_number_upload/", data, format="multipart")
        if user:
            force_authenticate(request, user=user)
        return upload_form_numbers(request)

    def test_stores_the_upload_and_reports_what_was_read(self):
        response = self._post({"source_type": "sctp", "file": _csv("form_number\n101\n101\nX\n")}, self.user)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["households"], response.data["duplicates"], response.data["rejected"]), (1, 1, 1))
        upload = MsrEtlUpload.objects.get(id=uuid.UUID(response.data["upload_id"]))
        self.assertEqual((upload.source_type, upload.form_numbers), ("sctp", ["101"]))

    def test_rejects_sources_that_do_not_take_files(self):
        response = self._post({"source_type": "pwp", "file": _csv("form_number\n101\n")}, self.user)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MsrEtlUpload.objects.exists())

    def test_reports_unreadable_files(self):
        response = self._post({"source_type": "sctp", "file": _csv("a,b\n1,2\n")}, self.user)

        self.assertEqual(response.status_code, 400)
        self.assertIn("No 'form_number' column", response.data["error"])

    def test_requires_authentication(self):
        response = self._post({"source_type": "sctp", "file": _csv("form_number\n101\n")})

        self.assertIn(response.status_code, (401, 403))
