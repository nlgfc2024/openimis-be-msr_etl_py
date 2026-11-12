import uuid
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from location.models import MsrLocationReference
from msr_etl.apps import MsrEtlConfig
from msr_etl.gql_mutations import ScheduleMsrUbrLocationsImportMutation, build_schedule_service_kwargs
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.services import UBRLocationService
from msr_etl.source_registry import list_individual_source_types, list_location_source_types
from msr_etl.sources.msr_geo_source import MsrGeoSource
from msr_etl.staging import stage_unit, sync_staged_units, requeue_retryable_failed_units
from msr_etl.scheduled_tasks import schedule_tasks


CONFIG = {"enabled": True, "auth_bearer_token": "test-only-token"}
DISTRICT = {"id": 13, "type": "districts", "code": "213", "name": "Dedza",
            "district_id": 13, "parents": [], "revision": 1}


class GeoSourceTests(SimpleTestCase):
    def setUp(self):
        patcher = patch.object(MsrEtlConfig, "sources", {"msr_geo_v3": CONFIG})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_source_is_location_only_and_opt_in(self):
        self.assertIn("msr_geo_v3", list_location_source_types())
        self.assertNotIn("msr_geo_v3", list_individual_source_types())
        with patch.object(MsrEtlConfig, "sources", {}):
            self.assertNotIn("msr_geo_v3", list_location_source_types())

    def test_service_construction_does_not_fetch_and_rejects_legacy_code_filter(self):
        with patch("msr_etl.sources.msr_v3_geo.create_retry_session") as session:
            service = UBRLocationService(Mock(), source_type="msr_geo_v3", msr_district_id=13)
            self.assertIsInstance(service.source, MsrGeoSource)
            session.return_value.get.assert_not_called()
        with self.assertRaises(ValueError):
            UBRLocationService(Mock(), source_type="msr_geo_v3", district="213")

    def test_invalid_ids_and_missing_credentials_rejected(self):
        for value in (None, 0, -1, 1.5, True, "abc"):
            with self.subTest(value=value), self.assertRaises(Exception):
                MsrGeoSource("msr_geo_v3", value)
        with patch.object(MsrEtlConfig, "sources", {"msr_geo_v3": {"enabled": True}}):
            with self.assertRaisesRegex(Exception, "Bearer token"):
                MsrGeoSource("msr_geo_v3", 13)

    def test_schedule_validates_filters_without_http(self):
        with patch("msr_etl.gql_mutations.run_as_scheduled_job") as schedule:
            result = ScheduleMsrUbrLocationsImportMutation._mutate(
                Mock(), source_type="msr_geo_v3", filters={"msr_district_id": 13},
            )
            self.assertIsNone(result)
            self.assertEqual(schedule.call_args.kwargs["params"], {"source_type": "msr_geo_v3", "msr_district_id": 13})
        with self.assertRaises(Exception):
            build_schedule_service_kwargs(UBRLocationService, "location", {
                "source_type": "msr_geo_v3", "filters": {"district": "213"},
            })

    def test_fetch_complete_snapshot_and_strip_access_links(self):
        source = MsrGeoSource("msr_geo_v3", 13)
        source.client = Mock()
        source.client.districts.return_value = [[{**DISTRICT, "geojson_url": "secret-signed-url"}]]
        catchment = {"id": 667, "type": "catchments", "district_id": 13, "is_micro": True, "revision": 2}
        source.client.locations.side_effect = [[], [], [], [[catchment]]]
        source.client.membership.return_value = [[{"id": 88, "catchment_id": 667}]]
        payload = source.fetch_unit(source.enumerate_units()[0])
        self.assertNotIn("geojson_url", payload["locations"][0])
        self.assertEqual(payload["memberships"], [{"catchment_id": 667, "revision": 2, "village_ids": [88]}])
        source.client.membership.assert_called_once_with(667, 13)

    def test_cross_district_rows_fail_snapshot(self):
        source = MsrGeoSource("msr_geo_v3", 13)
        source.client = Mock()
        source.client.districts.return_value = [[DISTRICT]]
        source.client.locations.return_value = [[{"type": "traditional_authorities", "district_id": 99}]]
        with self.assertRaisesRegex(Exception, "outside"):
            source.fetch_unit(source.enumerate_units()[0])

    def test_scheduler_can_register_before_configuration_ready(self):
        with patch.object(MsrEtlConfig, "sync_sweep_interval_minutes", None):
            scheduler = Mock()
            schedule_tasks(scheduler)
            self.assertEqual(scheduler.add_job.call_count, 2)


class GeoStagingTests(TestCase):
    def setUp(self):
        patcher = patch.object(MsrEtlConfig, "sources", {"msr_geo_v3": CONFIG})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.job = uuid.uuid4()
        self.source = MsrGeoSource("msr_geo_v3", 13)
        self.source.fetch_unit = Mock(return_value={"source_instance": "msr-malawi", "locations": [DISTRICT], "memberships": []})

    def test_stage_and_sync_use_dedicated_writer_and_correct_counts(self):
        count = stage_unit(self.job, self.source, self.source.enumerate_units()[0], MsrEtlSyncUnit.Kind.LOCATION)
        self.assertEqual(count, 1)
        reporter = Mock()
        with patch("msr_etl.staging.LocationImportSink") as legacy:
            sync_staged_units(self.job, user=Mock(), reporter=reporter)
            legacy.assert_not_called()
        self.assertEqual(MsrLocationReference.objects.get().msr_id, 13)
        self.assertEqual(MsrEtlSyncUnit.objects.get().sync_status, "SYNCED")
        reporter.advance.assert_called_once_with(k=1, synced=1)

    def test_failed_membership_write_rolls_back_references_and_retries_without_fetch(self):
        stage_unit(self.job, self.source, self.source.enumerate_units()[0], MsrEtlSyncUnit.Kind.LOCATION)
        with patch("location.msr_reference.MsrReferenceWriter.replace_memberships", side_effect=ValueError("bad membership")):
            sync_staged_units(self.job, user=Mock())
        self.assertFalse(MsrLocationReference.objects.exists())
        self.assertEqual(MsrEtlSyncUnit.objects.get().sync_status, "FAILED")
        requeue_retryable_failed_units(self.job)
        sync_staged_units(self.job, user=Mock())
        self.assertEqual(MsrEtlSyncUnit.objects.get().sync_status, "SYNCED")
        self.source.fetch_unit.assert_called_once()

    def test_download_failure_cannot_become_empty_success(self):
        self.source.fetch_unit.side_effect = ValueError("delivery failure")
        self.assertIsNone(stage_unit(self.job, self.source, self.source.enumerate_units()[0], MsrEtlSyncUnit.Kind.LOCATION))
        sync_staged_units(self.job, user=Mock())
        self.assertEqual(MsrEtlSyncUnit.objects.get().stage_status, "FAILED")
        self.assertFalse(MsrLocationReference.objects.exists())

    def test_source_configuration_change_cannot_redirect_staged_identity(self):
        stage_unit(self.job, self.source, self.source.enumerate_units()[0], MsrEtlSyncUnit.Kind.LOCATION)
        with patch.object(MsrEtlConfig, "sources", {"msr_geo_v3": {**CONFIG, "source_instance": "other-msr"}}):
            sync_staged_units(self.job, user=Mock())
        self.assertFalse(MsrLocationReference.objects.exists())
        self.assertEqual(MsrEtlSyncUnit.objects.get().sync_status, "FAILED")
