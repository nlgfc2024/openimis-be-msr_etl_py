import csv
import io
import os
import tempfile
import uuid
from unittest.mock import patch

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.test_helpers import LogInHelper
from location.models import Location
from msr_etl.apps import MsrEtlConfig
from msr_etl.location_file import (
    TEMPORARY_CODE_KEY, LocationFileError, current_location_code, read_location_file, resolve_location_codes,
)
from msr_etl.models import MsrEtlLocationCodeChange, MsrEtlSyncUnit, MsrEtlUpload
from msr_etl.services.ubr_service import UBRLocationService
from msr_etl.sources import ExcelLocationSource
from msr_etl.staging import stage_unit, sync_staged_units
from msr_etl.views import upload_import_file

CITY_CODES = {"Other City": "OC"}
COUNCIL_CONFIG = {"connector": "excel_locations", "kind": "location", "city_codes": CITY_CODES}
HEADER = ("city_code,city_name,constituency_code,constituency_name,ward_code,ward_name,"
          "neighbourhood_code,neighbourhood_name\n")
# Codes are partly missing, as in the councils' lists
ROWS = (
    "901,Test City,90101,Test Central,9010101,Test Ward A,9010101001,Test Area 1\n"
    "901,Test City,,Test Central ,,Test Ward A,,Test Area 2\n"
    "901,Test City,,Test East,,Test Ward B,,Test Area 3\n"
    ",Other City,,Other Central,,Other Ward,,Other Area\n"
)


def _csv(text, name="locations.csv"):
    return SimpleUploadedFile(name, text.encode())


def _xlsx(sheets, name="locations.xlsx"):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer) as writer:
        for sheet, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return SimpleUploadedFile(name, buffer.getvalue())


def _read(text):
    return read_location_file(_csv(text), city_codes=CITY_CODES)


def _codes(locations, location_type):
    return {row["name"]: row["code"] for row in locations if row["type"] == location_type}


def _active(code, location_type):
    return Location.objects.get(code=code, type=location_type, validity_to__isnull=True)


class ReadLocationFileTestCase(SimpleTestCase):

    def test_reads_each_place_once_with_the_codes_given(self):
        result = _read(HEADER + ROWS)

        self.assertEqual(result["level_counts"], {"R": 2, "D": 3, "W": 3, "V": 4})
        places = {place["name"]: place for place in result["places"]}
        self.assertEqual((places["Test Central"]["code"], places["Test East"]["code"]), ("90101", ""))
        self.assertEqual(places["Test Area 2"]["parent"], places["Test Ward A"]["id"])
        # A city without a code takes its configured temporary one
        self.assertEqual((places["Other City"]["code"], places["Other City"]["temporary"]), ("OC", True))
        self.assertEqual((result["row_count"], result["rejected_count"]), (4, 0))

    def test_a_code_on_a_later_row_applies_to_the_whole_place(self):
        result = _read(HEADER + "901,Test City,,Test Central,,Ward,,Area 1\n"
                                "901,Test City,90105,Test Central,,Ward,,Area 2\n")

        self.assertEqual({p["name"]: p["code"] for p in result["places"]}["Test Central"], "90105")

    def test_rejects_conflicting_and_temporary_looking_codes(self):
        result = _read(HEADER + ROWS
                       + "901,Test City,90199,Test Central,,Test Ward A,,Test Area 9\n"
                       + "901,Test City,90101,Test West,,Ward W,,Test Area 10\n"
                       + "901,Test City,90101T01,Test Central,,Test Ward A,,Test Area 11\n")

        self.assertEqual([row["row"] for row in result["rejected_rows"]], [6, 7, 8])
        reasons = [row["reason"] for row in result["rejected_rows"]]
        self.assertIn("constituency_name 'Test Central' has code 90101 on row 2", reasons[0])
        self.assertIn("constituency_code 90101 is already used for 'Test Central'", reasons[1])
        self.assertIn("looks like a temporary code", reasons[2])

    def test_rejects_missing_names_and_city_codes_and_skips_blank_rows(self):
        result = _read(HEADER + ROWS + ",,,,,,,\n"
                       + "901,Test City,,Test Central,,Test Ward A,,\n"
                       + ",Unknown City,,C,,W,,N\n"
                       + f"901,Test City,,Test Central,,Test Ward A,,{'x' * 51}\n")

        self.assertEqual([row["reason"] for row in result["rejected_rows"]], [
            "neighbourhood_name is empty",
            "city_code is empty and no temporary code is set for 'Unknown City'",
            "neighbourhood_name is longer than 50 characters",
        ])
        self.assertEqual(result["row_count"], 7)

    def test_code_columns_are_optional(self):
        result = _read("city_name,constituency_name,ward_name,neighbourhood_name\nOther City,C,W,N\n")

        self.assertEqual(len(result["places"]), 4)

    def test_reads_the_locations_sheet_of_the_template(self):
        df = pd.read_csv(io.StringIO(HEADER + ROWS), dtype=str, keep_default_na=False)
        result = read_location_file(_xlsx({"README": pd.DataFrame({"note": ["read me"]}), "locations": df}),
                                    city_codes=CITY_CODES)

        self.assertEqual(len(result["places"]), 12)

    def test_reports_missing_columns(self):
        with self.assertRaisesRegex(LocationFileError, "Missing columns: constituency_name"):
            read_location_file(_csv("city_name,ward_name,neighbourhood_name\n"))

    def test_fails_when_no_row_is_usable(self):
        with self.assertRaises(LocationFileError) as error:
            read_location_file(_csv(HEADER + "901,Test City,,,,,,\n"))
        self.assertEqual(error.exception.rejected_count, 1)


class ResolveLocationCodesTestCase(TestCase):

    def _resolve(self, text):
        return resolve_location_codes(_read(text)["places"])

    def test_missing_codes_get_temporary_ones_that_cannot_collide_with_real_codes(self):
        result = self._resolve(HEADER + ROWS)

        self.assertEqual(_codes(result["locations"], "D"),
                         {"Test Central": "90101", "Test East": "901T01", "Other Central": "OCT01"})
        self.assertEqual(_codes(result["locations"], "V"),
                         {"Test Area 1": "9010101001", "Test Area 2": "9010101T001",
                          "Test Area 3": "901T01T01T001", "Other Area": "OCT01T01T001"})
        temporary = {row["name"] for row in result["locations"] if row["temporary"]}
        self.assertNotIn("Test Area 1", temporary)
        self.assertIn("Other City", temporary)
        self.assertEqual((result["generated_codes"], result["conflicts"]), (7, []))

    def test_reuses_the_codes_of_places_already_in_the_mis(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        Location.objects.create(code="90107", name="TEST EAST", type="D", parent=city)
        Location.objects.create(code="901T05", name="Old Temporary", type="D", parent=city,
                                json_ext={TEMPORARY_CODE_KEY: True})

        result = self._resolve(HEADER + ROWS)

        self.assertEqual(_codes(result["locations"], "D")["Test East"], "90107")
        # Generated after the temporary codes already in use
        result = self._resolve(HEADER + "901,Test City,,New Place,,W,,N\n")
        self.assertEqual(_codes(result["locations"], "D")["New Place"], "901T06")

    def test_a_real_code_replaces_a_temporary_one_and_reports_it(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        Location.objects.create(code="901T01", name="Test East", type="D", parent=city,
                                json_ext={TEMPORARY_CODE_KEY: True})

        result = self._resolve(HEADER + "901,Test City,90102,Test East,,Ward,,Area\n")

        east = next(row for row in result["locations"] if row["name"] == "Test East")
        self.assertEqual((east["code"], east["previous_code"], east["temporary"]), ("90102", "901T01", False))
        self.assertEqual(result["code_changes"], [{"type": "D", "name": "Test East", "from": "901T01", "to": "90102"}])

    def test_real_codes_never_change(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        Location.objects.create(code="90101", name="Test Central", type="D", parent=city)

        result = self._resolve(HEADER + "901,Test City,90109,Test Central,,W,,N\n")

        self.assertEqual(len(result["conflicts"]), 1)
        self.assertIn("already has code 90101 in the MIS", result["conflicts"][0]["reason"])
        # Left out with everything under it
        self.assertEqual(_codes(result["locations"], "D"), {})
        self.assertEqual(_codes(result["locations"], "V"), {})

    def test_a_place_without_a_free_temporary_code_is_left_out_alone(self):
        # Wards take two digits, so the 100th uncoded ward under one constituency has no code left
        rows = "".join(f"901,Test City,90101,Test Central,,Ward {n},,Area {n}\n" for n in range(1, 101))

        result = self._resolve(HEADER + rows)

        self.assertEqual(len(_codes(result["locations"], "W")), 99)
        self.assertEqual([(c["value"], c["reason"]) for c in result["conflicts"]],
                         [("Ward 100", "no free temporary code left under 90101")])
        self.assertNotIn("Area 100", _codes(result["locations"], "V"))

    def test_a_code_given_for_another_name_is_reported_as_a_rename(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        Location.objects.create(code="90101", name="Old Name", type="D", parent=city)

        result = self._resolve(HEADER + "901,Test City,90101,New Name,,W,,N\n")

        self.assertEqual(result["renamed"], [{"type": "D", "code": "90101", "from": "Old Name", "to": "New Name"}])
        self.assertEqual(result["renamed_count"], 1)


@patch.object(MsrEtlConfig, "sources", {"council": COUNCIL_CONFIG})
class ExcelLocationImportTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()

    def _import(self, text):
        upload = MsrEtlUpload.objects.create(source_type="council", file_name="locations.csv",
                                             locations=_read(text)["places"])
        job_uuid = uuid.uuid4()
        source = UBRLocationService(self.user, source_type="council", upload_id=str(upload.id)).source
        self.assertIsInstance(source, ExcelLocationSource)
        units = source.enumerate_units()
        for unit in units:
            stage_unit(job_uuid, source, unit, MsrEtlSyncUnit.Kind.LOCATION)
        sync_staged_units(job_uuid, user=self.user)
        self.assertFalse(MsrEtlSyncUnit.objects.filter(job_uuid=job_uuid).exclude(
            sync_status=MsrEtlSyncUnit.Status.SYNCED).exists())
        return units

    def test_imports_each_city_top_down_and_flags_temporary_codes(self):
        units = self._import(HEADER + ROWS)

        self.assertEqual([(unit["unit_code"], unit["unit_type"]) for unit in units][:4],
                         [("901", "DISTRICT"), ("901", "TA"), ("901", "GVH"), ("901", "VILLAGE")])
        area = _active("901T01T01T001", "V")
        self.assertEqual((area.parent.code, area.parent.parent.code, area.parent.parent.parent.code),
                         ("901T01T01", "901T01", "901"))
        self.assertTrue(area.json_ext[TEMPORARY_CODE_KEY])
        self.assertNotIn(TEMPORARY_CODE_KEY, _active("9010101001", "V").json_ext or {})

    def test_reimporting_the_same_file_changes_nothing(self):
        self._import(HEADER + ROWS)
        count = Location.objects.filter(validity_to__isnull=True).count()

        self._import(HEADER + ROWS)

        self.assertEqual(Location.objects.filter(validity_to__isnull=True).count(), count)
        self.assertFalse(MsrEtlLocationCodeChange.objects.exists())

    def test_real_codes_replace_temporary_ones_on_the_same_locations(self):
        self._import(HEADER + ROWS + "901,Test City,,Test East,,Test Ward B,,Not In Next File\n")
        east, area = _active("901T01", "D"), _active("901T01T01T001", "V")
        left_out = _active("901T01T01T002", "V")

        # The council's next list has real codes for Test East and Test Area 3 only
        self._import(HEADER + "901,Test City,90102,Test East,,Test Ward B,9010201293,Test Area 3\n")

        self.assertEqual((_active("90102", "D").id, _active("9010201293", "V").id), (east.id, area.id))
        ward_b = _active("90102T01", "W")
        self.assertEqual(ward_b.parent_id, east.id)
        # Not in the file, but its temporary code follows its parent's new one
        self.assertEqual(_active("90102T01T002", "V").id, left_out.id)
        self.assertNotIn(TEMPORARY_CODE_KEY, _active("90102", "D").json_ext)
        self.assertTrue(ward_b.json_ext[TEMPORARY_CODE_KEY])
        # Codes written before the change still lead to the location
        self.assertEqual(current_location_code("V", "901T01T01T001"), "9010201293")
        self.assertEqual(current_location_code("V", "901T01T01T002"), "90102T01T002")
        self.assertEqual(current_location_code("V", "9010101001"), "9010101001")
        self.assertEqual(Location.objects.filter(name="Test East", validity_to__isnull=True).count(), 1)

    def test_a_replaced_prefix_and_a_new_place_never_get_the_same_code(self):
        self._import(HEADER + "901,Test City,,Test East,,Test Ward B,,Old Area\n")
        old_area = _active("901T01T01T001", "V")

        # The ward gets its real code; Old Area is left out and New Area has no code yet
        self._import(HEADER + "901,Test City,,Test East,9010101,Test Ward B,,New Area\n")

        self.assertEqual(_active("9010101T001", "V").id, old_area.id)
        new_area = Location.objects.get(name="New Area", validity_to__isnull=True)
        self.assertEqual((new_area.code, new_area.parent.code), ("9010101T002", "9010101"))
        self.assertEqual(Location.objects.filter(name="Old Area", validity_to__isnull=True).count(), 1)

    def test_exports_the_kobo_choice_files(self):
        self._import(HEADER + ROWS)

        with tempfile.TemporaryDirectory() as out:
            call_command("export_kobo_location_choices", out=out, cities=["901"], stdout=io.StringIO())
            files = {name: list(csv.DictReader(open(os.path.join(out, name), encoding="utf-8")))
                     for name in ("cities.csv", "wards.csv", "neighbourhoods.csv")}

        self.assertEqual(files["cities.csv"], [{"name": "901", "label": "Test City"}])
        self.assertEqual(sorted(ward["name"] for ward in files["wards.csv"]), ["9010101", "901T01T01"])
        self.assertIn({"name": "901T01T01T001", "label": "Test Area 3", "city": "901", "ward": "901T01T01",
                       "location_name": "Test Area 3"}, files["neighbourhoods.csv"])


@patch.object(MsrEtlConfig, "sources", {"council": COUNCIL_CONFIG})
class UploadLocationFileViewTestCase(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()

    def _post(self, data):
        request = APIRequestFactory().post("/api/msr_etl/upload/", data, format="multipart")
        force_authenticate(request, user=self.user)
        return upload_import_file(request)

    def test_stores_the_places_and_previews_the_import(self):
        response = self._post({"source_type": "council", "file": _csv(HEADER + ROWS)})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["records"], response.data["rejected"], response.data["generated_codes"],
                          response.data["code_changes_count"]), (12, 0, 7, 0))
        upload = MsrEtlUpload.objects.get(id=uuid.UUID(response.data["upload_id"]))
        self.assertEqual((len(upload.locations), upload.form_numbers), (12, []))

    def test_reports_conflicts_with_the_mis_as_rejected(self):
        city = Location.objects.create(code="901", name="Test City", type="R")
        Location.objects.create(code="90101", name="Test Central", type="D", parent=city)

        response = self._post({"source_type": "council",
                               "file": _csv(HEADER + "901,Test City,90109,Test Central,,W,,N\n")})

        self.assertEqual((response.status_code, response.data["rejected"]), (200, 1))
        self.assertIn("real codes don't change", response.data["rejected_rows"][0]["reason"])

    def test_refuses_a_turned_off_source(self):
        with patch.object(MsrEtlConfig, "sources", {"council": {**COUNCIL_CONFIG, "enabled": False}}):
            response = self._post({"source_type": "council", "file": _csv(HEADER + ROWS)})

        self.assertEqual((response.status_code, response.data["error"]), (400, "Source 'council' is turned off"))
        self.assertFalse(MsrEtlUpload.objects.exists())

    def test_reports_an_unusable_file(self):
        response = self._post({"source_type": "council", "file": _csv("a,b\n1,2\n")})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing columns", response.data["error"])


class DisabledSourceTestCase(SimpleTestCase):

    @patch.object(MsrEtlConfig, "sources", {"ubr": {"enabled": False}, "council": COUNCIL_CONFIG})
    def test_a_disabled_source_is_neither_listed_nor_resolved(self):
        from msr_etl.source_registry import UnknownSourceType, list_location_source_types, resolve_location_source

        self.assertEqual(list_location_source_types(), ["council"])
        with self.assertRaises(UnknownSourceType):
            resolve_location_source("ubr")

    @patch.object(MsrEtlConfig, "sources", {"ubr": {"enabled": False}})
    def test_the_msr_queries_refuse_a_disabled_ubr_source(self):
        from unittest.mock import MagicMock
        from msr_etl.schema import Query

        info = MagicMock()
        info.context.user.has_perms.return_value = True
        for resolve in (Query.resolve_msr_ubr_individuals, Query.resolve_msr_ubr_locations):
            with self.assertRaisesRegex(ValueError, "'ubr' source is turned off"):
                resolve(None, info)
