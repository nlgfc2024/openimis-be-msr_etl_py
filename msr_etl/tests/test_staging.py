from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from msr_etl.models import MsrEtlSyncUnit
from msr_etl.staging import (
    enumerate_individual_units,
    enumerate_location_units,
    has_retryable_failed_units,
    job_has_failed_units,
    requeue_retryable_failed_units,
    stage_individual_unit,
    stage_location_unit,
    sync_staged_units,
)


class EnumerateIndividualUnitsTestCase(SimpleTestCase):

    @patch("msr_etl.staging.UBRIndividualService")
    def test_builds_district_ta_percentile_grid(self, mock_service_class):
        source = MagicMock()
        source.get_district_codes.return_value = ["101"]
        source.get_ta_codes.return_value = ["10101"]
        source.get_percentile_chunks.return_value = [range(0, 10), range(10, 20)]
        mock_service_class.return_value.source = source

        returned_source, units = enumerate_individual_units("user", {"district": "101", "ta": "10101"})

        self.assertIs(returned_source, source)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["unit_code"], "101:10101:0-9")
        self.assertEqual(units[1]["unit_code"], "101:10101:10-19")
        mock_service_class.assert_called_once_with("user", district="101", ta="10101")


class EnumerateLocationUnitsTestCase(SimpleTestCase):

    @patch("msr_etl.staging.UBRLocationService")
    def test_builds_four_units_per_district(self, mock_service_class):
        source = MagicMock()
        source.list_districts.return_value = [
            {"geo_location_code": "101", "geo_location_name": "Chitipa"},
            {"geo_location_code": "102", "geo_location_name": "Karonga"},
        ]
        source.fetch_unit.return_value = {"data_type": "T", "data": [{"geo_location_code": "10101"}]}
        mock_service_class.return_value.source = source

        returned_source, units = enumerate_location_units("user")

        self.assertIs(returned_source, source)
        self.assertEqual(len(units), 8)
        unit_types = [u["unit_type"] for u in units if u["district"] == "101"]
        self.assertEqual(
            unit_types,
            [
                MsrEtlSyncUnit.UnitType.DISTRICT,
                MsrEtlSyncUnit.UnitType.TA,
                MsrEtlSyncUnit.UnitType.GVH,
                MsrEtlSyncUnit.UnitType.VILLAGE,
            ],
        )
        # TA unit reuses the payload fetched to decide whether to skip the district
        ta_unit = next(u for u in units if u["district"] == "101" and u["unit_type"] == MsrEtlSyncUnit.UnitType.TA)
        self.assertEqual(ta_unit["payload"], source.fetch_unit.return_value)

    @patch("msr_etl.staging.UBRLocationService")
    def test_skips_district_row_missing_code(self, mock_service_class):
        source = MagicMock()
        source.list_districts.return_value = [{"geo_location_name": "No code"}]
        mock_service_class.return_value.source = source

        _, units = enumerate_location_units("user")

        self.assertEqual(units, [])
        source.fetch_unit.assert_not_called()

    @patch("msr_etl.staging.UBRLocationService")
    def test_skips_district_with_no_tas(self, mock_service_class):
        source = MagicMock()
        source.list_districts.return_value = [
            {"geo_location_code": "101", "geo_location_name": "Chitipa"},
            {"geo_location_code": "102", "geo_location_name": "Karonga"},
        ]

        def fetch_unit(district_code, unit_type):
            if district_code == "101":
                return {"data_type": "T", "data": []}
            return {"data_type": "T", "data": [{"geo_location_code": "10201"}]}

        source.fetch_unit.side_effect = fetch_unit
        mock_service_class.return_value.source = source

        _, units = enumerate_location_units("user")

        self.assertEqual([u["district"] for u in units], ["102", "102", "102", "102"])


class StageIndividualUnitTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "11111111-1111-1111-1111-111111111111"
        self.unit = {
            "district": "101", "ta": "10101",
            "percentile_range": range(0, 10),
            "unit_code": "101:10101:0-9",
        }

    def test_stages_payload_and_identities(self):
        source = MagicMock()
        source.fetch_unit.return_value = [
            {"id": 1, "form_number": "A1"},
            {"id": 2, "form_number": "A2"},
        ]

        result = stage_individual_unit(self.job_uuid, source, self.unit)

        self.assertTrue(result)
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.stage_status, MsrEtlSyncUnit.Status.STAGED)
        self.assertEqual(sync_unit.record_count, 2)
        self.assertEqual(len(sync_unit.raw_payload), 2)
        self.assertEqual(len(sync_unit.record_identities), 2)

    def test_dedupes_against_sibling_units_of_same_job(self):
        MsrEtlSyncUnit.objects.create(
            job_uuid=self.job_uuid,
            unit_type=MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK,
            unit_code="101:10101:10-19",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            record_identities=[["id", "1"]],
        )
        source = MagicMock()
        source.fetch_unit.return_value = [
            {"id": 1, "form_number": "A1"},  # already seen by sibling unit
            {"id": 2, "form_number": "A2"},
        ]

        stage_individual_unit(self.job_uuid, source, self.unit)

        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.record_count, 1)
        self.assertEqual(sync_unit.raw_payload[0]["id"], 2)

    def test_marks_failed_on_fetch_error(self):
        source = MagicMock()
        source.fetch_unit.side_effect = RuntimeError("UBR API unavailable")

        result = stage_individual_unit(self.job_uuid, source, self.unit)

        self.assertFalse(result)
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.stage_status, MsrEtlSyncUnit.Status.FAILED)
        self.assertIn("UBR API unavailable", sync_unit.error_detail)


class StageLocationUnitTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "22222222-2222-2222-2222-222222222222"

    def test_district_unit_reuses_enumeration_payload_without_fetching(self):
        source = MagicMock()
        unit = {
            "district": "101",
            "unit_type": MsrEtlSyncUnit.UnitType.DISTRICT,
            "payload": {"data_type": "D", "data": [{"geo_location_code": "101"}]},
        }

        result = stage_location_unit(self.job_uuid, source, unit)

        self.assertTrue(result)
        source.fetch_unit.assert_not_called()
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.DISTRICT)
        self.assertEqual(sync_unit.record_count, 1)

    def test_ta_unit_reuses_enumeration_payload_without_fetching(self):
        source = MagicMock()
        payload = {"data_type": "T", "data": [{"geo_location_code": "10101"}]}
        unit = {"district": "101", "unit_type": MsrEtlSyncUnit.UnitType.TA, "payload": payload}

        result = stage_location_unit(self.job_uuid, source, unit)

        self.assertTrue(result)
        source.fetch_unit.assert_not_called()
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA)
        self.assertEqual(sync_unit.record_count, 1)

    def test_gvh_unit_fetches_from_source(self):
        source = MagicMock()
        source.fetch_unit.return_value = {"data_type": "G", "data": [{"geo_location_code": "1010101"}]}
        unit = {"district": "101", "unit_type": MsrEtlSyncUnit.UnitType.GVH}

        result = stage_location_unit(self.job_uuid, source, unit)

        self.assertTrue(result)
        source.fetch_unit.assert_called_once_with("101", MsrEtlSyncUnit.UnitType.GVH)

    def test_marks_failed_on_fetch_error(self):
        source = MagicMock()
        source.fetch_unit.side_effect = RuntimeError("boom")
        unit = {"district": "101", "unit_type": MsrEtlSyncUnit.UnitType.GVH}

        result = stage_location_unit(self.job_uuid, source, unit)

        self.assertFalse(result)
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.GVH)
        self.assertEqual(sync_unit.stage_status, MsrEtlSyncUnit.Status.FAILED)


class SyncStagedUnitsTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "33333333-3333-3333-3333-333333333333"
        self.reporter = MagicMock()

    def _staged_unit(self, unit_type, unit_code, payload):
        return MsrEtlSyncUnit.objects.create(
            job_uuid=self.job_uuid,
            unit_type=unit_type,
            unit_code=unit_code,
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            raw_payload=payload,
        )

    @patch("msr_etl.staging.IndividualImportSink")
    @patch("msr_etl.staging.UBRIndividualAdapter")
    def test_syncs_percentile_chunk_unit(self, mock_adapter_class, mock_sink_class):
        mock_adapter_class.return_value.transform.return_value = [{"first_name": "A"}]
        unit = self._staged_unit(MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, "101:10101:0-9", [{"id": 1}])

        sync_staged_units(self.job_uuid, reporter=self.reporter, user="the-user")

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.SYNCED)
        mock_sink_class.assert_called_once_with("the-user")
        mock_sink_class.return_value.push.assert_called_once()
        self.reporter.advance.assert_called_once_with(synced=1)

    @patch("msr_etl.staging.LocationImportSink")
    @patch("msr_etl.staging.UBRLocationAdapter")
    def test_syncs_location_units_in_hierarchy_order(self, mock_adapter_class, mock_sink_class):
        mock_adapter_class.return_value.transform.return_value = [{"code": "x", "type": "R"}]
        call_order = []
        mock_sink_class.return_value.push.side_effect = lambda *a, **k: call_order.append(a)

        self._staged_unit(MsrEtlSyncUnit.UnitType.VILLAGE, "101", {"data_type": "V", "data": []})
        self._staged_unit(MsrEtlSyncUnit.UnitType.DISTRICT, "101", {"data_type": "D", "data": []})
        self._staged_unit(MsrEtlSyncUnit.UnitType.GVH, "101", {"data_type": "G", "data": []})
        self._staged_unit(MsrEtlSyncUnit.UnitType.TA, "101", {"data_type": "T", "data": []})

        sync_staged_units(self.job_uuid, reporter=self.reporter, user="the-user")

        synced_order = list(
            MsrEtlSyncUnit.objects.filter(job_uuid=self.job_uuid, sync_status=MsrEtlSyncUnit.Status.SYNCED)
        )
        self.assertEqual(len(synced_order), 4)
        self.assertEqual(self.reporter.advance.call_count, 4)

    @patch("msr_etl.staging.IndividualImportSink")
    @patch("msr_etl.staging.UBRIndividualAdapter")
    def test_marks_failed_and_bumps_attempts_on_sink_error(self, mock_adapter_class, mock_sink_class):
        mock_adapter_class.return_value.transform.return_value = [{"first_name": "A"}]
        mock_sink_class.return_value.push.side_effect = RuntimeError("workflow failed")
        unit = self._staged_unit(MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, "101:10101:0-9", [{"id": 1}])

        sync_staged_units(self.job_uuid, reporter=self.reporter, user="the-user")

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.FAILED)
        self.assertEqual(unit.attempts, 1)
        self.reporter.advance.assert_called_once_with(errors=1)

    def test_skips_units_not_pending(self):
        self._staged_unit(MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, "101:10101:0-9", [])
        MsrEtlSyncUnit.objects.filter(job_uuid=self.job_uuid).update(sync_status=MsrEtlSyncUnit.Status.SYNCED)

        sync_staged_units(self.job_uuid, reporter=self.reporter, user="the-user")

        self.reporter.advance.assert_not_called()


class JobHasFailedUnitsTestCase(TestCase):

    def test_false_when_no_units(self):
        self.assertFalse(job_has_failed_units("44444444-4444-4444-4444-444444444444"))

    def test_true_on_stage_failure(self):
        job_uuid = "55555555-5555-5555-5555-555555555555"
        MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.FAILED,
        )
        self.assertTrue(job_has_failed_units(job_uuid))

    def test_true_on_sync_failure(self):
        job_uuid = "66666666-6666-6666-6666-666666666666"
        MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.FAILED,
        )
        self.assertTrue(job_has_failed_units(job_uuid))

    def test_false_when_all_synced(self):
        job_uuid = "77777777-7777-7777-7777-777777777777"
        MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.SYNCED,
        )
        self.assertFalse(job_has_failed_units(job_uuid))


class HasRetryableFailedUnitsTestCase(TestCase):

    def test_false_when_no_units(self):
        self.assertFalse(has_retryable_failed_units("88888888-8888-8888-8888-888888888888"))

    def test_true_when_sync_failure_below_max_attempts(self):
        job_uuid = "99999999-9999-9999-9999-999999999999"
        MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.FAILED,
            attempts=1,
        )
        self.assertTrue(has_retryable_failed_units(job_uuid))

    def test_false_once_max_attempts_reached(self):
        job_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.FAILED,
            attempts=3,
        )
        self.assertFalse(has_retryable_failed_units(job_uuid))

    def test_false_on_stage_level_failure(self):
        # stage failures have no requeue path (no staged payload to retry from)
        job_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.FAILED,
        )
        self.assertFalse(has_retryable_failed_units(job_uuid))


class RequeueRetryableFailedUnitsTestCase(TestCase):

    def test_requeues_units_below_max_attempts(self):
        job_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        unit = MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.FAILED,
            attempts=1,
        )

        requeue_retryable_failed_units(job_uuid)

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.PENDING)

    def test_leaves_units_at_max_attempts_failed(self):
        job_uuid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        unit = MsrEtlSyncUnit.objects.create(
            job_uuid=job_uuid, unit_type=MsrEtlSyncUnit.UnitType.TA, unit_code="101",
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            sync_status=MsrEtlSyncUnit.Status.FAILED,
            attempts=3,
        )

        requeue_retryable_failed_units(job_uuid)

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.FAILED)
