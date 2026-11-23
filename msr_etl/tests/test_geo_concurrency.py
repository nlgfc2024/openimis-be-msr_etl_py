"""Exercise the actual PostgreSQL source/district lock with two connections."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import uuid

from django.db import connection, connections
from msr_etl.models import MsrGeoSyncState
from msr_etl.geo_synchronization import apply_unit, prepare_receipt


class ConcurrentSyncTests(TestCase):
    def test_district_writes_serialize_across_worker_connections(self):
        config = {"source_instance": "concurrent-fixture", "base_url": "http://localhost:8080", "auth_bearer_token": "fixture"}
        source = SimpleNamespace(policy="msr_etl", source_type="msr_geo_v3", source_instance=config["source_instance"],
                                 district_id=13, client=SimpleNamespace(config=config))
        def unit(pk):
            value = SimpleNamespace(pk=pk, job_uuid=uuid.uuid4(), source_type="msr_geo_v3", unit_code="13")
            value.raw_payload = {"source_instance": source.source_instance,
                "locations": [{"id": 13, "type": "districts", "district_id": 13}], "memberships": [],
                "_msr_geo": prepare_receipt(source, value)}
            return value
        with connection.cursor() as cursor:
            cursor.execute("SHOW search_path")
            path = cursor.fetchone()[0]
        older_entered, newer_started, release, newer_written = Event(), Event(), Event(), Event()
        order = []
        def write(rows):
            if current_thread().name.endswith("_0"):
                older_entered.set()
                if not release.wait(10):
                    raise RuntimeError("Test release timed out")
                order.append(10)
            else:
                newer_written.set()
                order.append(20)
        def worker(value):
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT set_config('search_path', %s, false)", [path])
                if value.pk == 20:
                    newer_started.set()
                return apply_unit(value, None)
            finally:
                connections["default"].close()
        with patch("msr_etl.geo_synchronization.refresh_configuration"), \
             patch("msr_etl.geo_synchronization.current_source", return_value=config), \
             patch("msr_etl.geo_synchronization.require_district_scope"), \
             patch("location.msr_reference.MsrReferenceWriter") as writer:
            writer.return_value.push.side_effect = write
            try:
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="mapping_lock") as pool:
                    older = pool.submit(worker, unit(10))
                    try:
                        self.assertTrue(older_entered.wait(5))
                        newer = pool.submit(worker, unit(20))
                        self.assertTrue(newer_started.wait(5))
                        self.assertFalse(newer_written.wait(0.2), "Newer writer bypassed district lock")
                    finally:
                        release.set()
                    self.assertEqual(older.result(timeout=10), 1)
                    self.assertEqual(newer.result(timeout=10), 1)
                self.assertEqual(order, [10, 20])
                self.assertEqual(MsrGeoSyncState.objects.get(source_instance=source.source_instance).last_applied_unit_id, 20)
            finally:
                MsrGeoSyncState.objects.filter(source_instance=source.source_instance).delete()
