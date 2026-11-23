"""Run from the configured CoreMIS shell; all writes use a disposable PG schema.

python manage.py shell -c 'from mapping.tests.run_host import run; run()'
"""
import unittest
import uuid
from unittest.mock import patch
from django.db import connection


def run():
    from location.models import MsrLocationReference, MsrCatchmentVillage, GeographicFeature, GeographicMembership
    from msr_etl.models import MsrGeoSyncState
    from msr_etl.models import MsrEtlSyncUnit
    from core.models import AsyncJob, User
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise RuntimeError("Run host checks on PostgreSQL outside an atomic block")
    name = "mapping_test_" + uuid.uuid4().hex
    quoted = connection.ops.quote_name(name)
    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        original_path = cursor.fetchone()[0]
        cursor.execute("CREATE SCHEMA " + quoted)
        cursor.execute("SET search_path TO " + quoted + ", public")
    from django.db.backends.signals import connection_created
    def set_test_schema(sender, connection, **kwargs):
        if connection.alias == 'default':
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO ' + quoted + ', public')
    connection_created.connect(set_test_schema, weak=False, dispatch_uid=name)
    try:
        with connection.schema_editor() as editor:
            for model in (MsrGeoSyncState, MsrLocationReference, MsrCatchmentVillage, MsrEtlSyncUnit, AsyncJob, GeographicFeature, GeographicMembership):
                editor.create_model(model)
        labels = ("msr_etl.tests.test_geo_coordination", "msr_etl.tests.test_geo_concurrency", "msr_etl.tests.test_local_geography", "msr_etl.tests.test_geometry_access", "msr_etl.tests.test_msr_v3_geo_client",
                  "msr_etl.tests.test_geo_sync", "msr_etl.tests.test_jobs",
                  "msr_etl.tests.test_staging", "msr_etl.tests.test_scheduled_tasks")
        from django.apps import apps
        if apps.is_installed("mapping"):
            labels += ("mapping.tests.test_store",)
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(label) for label in labels)
        # Reuse an existing actor read-only; the shared sweeper fixture normally
        # creates users in its disposable test database. Here jobs alone need FK.
        actor = User.objects.filter(i_user__isnull=False).first()
        if actor is None:
            raise RuntimeError("Host checks require an existing CoreMIS interactive actor")
        with patch("msr_etl.tests.test_scheduled_tasks.create_test_interactive_user", return_value=actor):
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError("Mapping host checks failed")
    finally:
        connection_created.disconnect(dispatch_uid=name)
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('search_path', %s, false)", [original_path])
            cursor.execute("DROP SCHEMA " + quoted + " CASCADE")
