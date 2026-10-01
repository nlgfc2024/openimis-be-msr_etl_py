from unittest.mock import MagicMock, patch

from django.test import TestCase

from msr_etl.models import MsrEtlSyncUnit
from msr_etl.sources.ubr_source import UBRIndividualSource
from msr_etl.staging import (
    has_retryable_failed_units,
    job_has_failed_units,
    requeue_retryable_failed_units,
    stage_unit,
    sync_staged_units,
)

INDIVIDUAL = MsrEtlSyncUnit.Kind.INDIVIDUAL
LOCATION = MsrEtlSyncUnit.Kind.LOCATION


def _source(source_type="ubr"):
    source = MagicMock()
    source.source_type = source_type
    source.record_identity.side_effect = UBRIndividualSource.get_household_identity
    source.count_records.side_effect = len
    return source


class StageUnitListPayloadTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "11111111-1111-1111-1111-111111111111"
        self.unit = {
            "unit_type": MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK,
            "unit_code": "101:10101:0-9",
            "district": "101", "ta": "10101",
            "percentile_range": range(0, 10),
        }

    def test_stages_payload_and_identities(self):
        source = _source("sctp")
        source.fetch_unit.return_value = [
            {"id": 1, "form_number": "A1"},
            {"id": 2, "form_number": "A2"},
        ]

        result = stage_unit(self.job_uuid, source, self.unit, INDIVIDUAL)

        self.assertTrue(result)
        source.fetch_unit.assert_called_once_with(self.unit)
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.stage_status, MsrEtlSyncUnit.Status.STAGED)
        self.assertEqual(sync_unit.source_type, "sctp")
        self.assertEqual(sync_unit.kind, INDIVIDUAL)
        self.assertEqual(sync_unit.unit_type, MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK)
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
        source = _source()
        source.fetch_unit.return_value = [
            {"id": 1, "form_number": "A1"},  # already seen by sibling unit
            {"id": 2, "form_number": "A2"},
        ]

        stage_unit(self.job_uuid, source, self.unit, INDIVIDUAL)

        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.record_count, 1)
        self.assertEqual(sync_unit.raw_payload[0]["id"], 2)

    def test_rows_without_identity_are_kept_and_not_recorded(self):
        source = _source()
        source.record_identity.side_effect = lambda row: None
        source.fetch_unit.return_value = [{"name": "a"}, {"name": "a"}]

        stage_unit(self.job_uuid, source, self.unit, INDIVIDUAL)

        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.record_count, 2)
        self.assertEqual(sync_unit.record_identities, [])

    def test_marks_failed_on_fetch_error(self):
        source = _source()
        source.fetch_unit.side_effect = RuntimeError("UBR API unavailable")

        result = stage_unit(self.job_uuid, source, self.unit, INDIVIDUAL)

        self.assertFalse(result)
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_code="101:10101:0-9")
        self.assertEqual(sync_unit.stage_status, MsrEtlSyncUnit.Status.FAILED)
        self.assertIn("UBR API unavailable", sync_unit.error_detail)


class StageUnitDictPayloadTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "22222222-2222-2222-2222-222222222222"
        self.unit = {"unit_type": MsrEtlSyncUnit.UnitType.GVH, "unit_code": "101", "district": "101"}

    def test_counts_records_under_data_and_skips_dedup(self):
        source = _source()
        source.count_records.side_effect = lambda payload: len(payload["data"])
        source.fetch_unit.return_value = {"data_type": "G", "data": [{"geo_location_code": "1010101"}]}

        result = stage_unit(self.job_uuid, source, self.unit, LOCATION)

        self.assertTrue(result)
        source.record_identity.assert_not_called()
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.GVH)
        self.assertEqual(sync_unit.kind, LOCATION)
        self.assertEqual(sync_unit.record_count, 1)
        self.assertIsNone(sync_unit.record_identities)

    def test_record_count_comes_from_the_source(self):
        source = _source()
        source.fetch_unit.return_value = {"rows": [1, 2, 3]}
        source.count_records.side_effect = None
        source.count_records.return_value = 42

        stage_unit(self.job_uuid, source, self.unit, LOCATION)

        source.count_records.assert_called_once_with({"rows": [1, 2, 3]})
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.GVH)
        self.assertEqual(sync_unit.record_count, 42)

    def test_marks_failed_on_fetch_error(self):
        source = _source()
        source.fetch_unit.side_effect = RuntimeError("boom")

        result = stage_unit(self.job_uuid, source, self.unit, LOCATION)

        self.assertFalse(result)
        sync_unit = MsrEtlSyncUnit.objects.get(job_uuid=self.job_uuid, unit_type=MsrEtlSyncUnit.UnitType.GVH)
        self.assertEqual(sync_unit.stage_status, MsrEtlSyncUnit.Status.FAILED)


class SyncStagedUnitsTestCase(TestCase):

    def setUp(self):
        self.job_uuid = "33333333-3333-3333-3333-333333333333"
        self.reporter = MagicMock()
        self.adapter_class = MagicMock()
        self.sink_class = MagicMock()
        resolver = MagicMock(return_value=(MagicMock(), self.adapter_class))
        self.resolver = resolver
        patchers = [
            patch.dict("msr_etl.staging._RESOLVERS", {INDIVIDUAL: resolver, LOCATION: resolver}),
            patch.dict("msr_etl.staging._SINKS", {INDIVIDUAL: self.sink_class, LOCATION: self.sink_class}),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _staged_unit(self, unit_type, unit_code, payload, kind=INDIVIDUAL, source_type="ubr"):
        return MsrEtlSyncUnit.objects.create(
            job_uuid=self.job_uuid,
            source_type=source_type,
            kind=kind,
            unit_type=unit_type,
            unit_code=unit_code,
            stage_status=MsrEtlSyncUnit.Status.STAGED,
            raw_payload=payload,
        )

    def test_syncs_unit_through_its_source_types_adapter(self):
        self.adapter_class.return_value.transform.return_value = [{"first_name": "A"}]
        unit = self._staged_unit(
            MsrEtlSyncUnit.UnitType.PERCENTILE_CHUNK, "101:10101:0-9", [{"id": 1}], source_type="sctp",
        )

        sync_staged_units(self.job_uuid, reporter=self.reporter, user="the-user")

        unit.refresh_from_db()
        self.assertEqual(unit.sync_status, MsrEtlSyncUnit.Status.SYNCED)
        self.resolver.assert_called_once_with("sctp")
        self.adapter_class.assert_called_once_with(source_type="sctp")
        self.sink_class.assert_called_once_with("the-user")
        self.sink_class.return_value.push.assert_called_once()
        self.reporter.advance.assert_called_once_with(synced=1)

    def test_syncs_location_units_in_hierarchy_order(self):
        self.adapter_class.return_value.transform.side_effect = lambda payload: [payload["data_type"]]
        pushed = []
        self.sink_class.return_value.push.side_effect = lambda records, _id: pushed.extend(records)

        for unit_type, data_type in (
            (MsrEtlSyncUnit.UnitType.VILLAGE, "V"),
            (MsrEtlSyncUnit.UnitType.DISTRICT, "D"),
            (MsrEtlSyncUnit.UnitType.GVH, "G"),
            (MsrEtlSyncUnit.UnitType.TA, "T"),
        ):
            self._staged_unit(unit_type, "101", {"data_type": data_type, "data": []}, kind=LOCATION)

        sync_staged_units(self.job_uuid, reporter=self.reporter, user="the-user")

        self.assertEqual(pushed, ["D", "T", "G", "V"])
        self.assertEqual(self.reporter.advance.call_count, 4)

    def test_marks_failed_and_bumps_attempts_on_sink_error(self):
        self.adapter_class.return_value.transform.return_value = [{"first_name": "A"}]
        self.sink_class.return_value.push.side_effect = RuntimeError("workflow failed")
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
