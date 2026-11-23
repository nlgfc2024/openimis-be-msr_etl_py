"""Local publication lifecycle and importer-independence checks in isolated PG tables."""
import importlib
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from django.apps import apps
from django.db import connection, transaction
from location.models import Location, MsrLocationReference, GeographicFeature
from location.geography import local_geometry
from msr_etl.geography import publish_snapshot
from msr_etl.models import MsrGeoSyncState

GEO = {'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{
    'type':'Polygon','coordinates':[[[33,-14],[34,-14],[34,-13],[33,-14]]]}}]}


class LocalGeographyTests(TestCase):
    def setUp(self):
        self.atomic=transaction.atomic();self.atomic.__enter__();self.addCleanup(self.rollback)
        self.local=Location.objects.filter(type='R',validity_to__isnull=True).first()
        self.ref=MsrLocationReference.objects.create(source_instance='fixture',msr_type='districts',msr_id=13,
            district_msr_id=13,name='Fixture district',code='213',location=self.local,revision=2,
            geometry_sha256='a'*64,boundary_status='available',geometry_relation='boundary')

    def rollback(self):
        transaction.set_rollback(True);self.atomic.__exit__(None,None,None)

    def publish(self, result):
        publish_snapshot('fixture',[{'type':'districts','id':13}],[],[
            {'type':'districts','id':13,'result':result}])
        return GeographicFeature.objects.get(source_instance='fixture')

    def accepted(self):
        return self.publish({'status':'available','revision':'2','sha256':'a'*64,
            'geojson':GEO,'relation':'boundary','source':'fixture'})

    def test_failed_delivery_preserves_last_accepted_geometry(self):
        before=self.accepted()
        after=self.publish({'status':'delivery_unavailable','revision':'3'})
        self.assertEqual(after.geometry,before.geometry)
        self.assertEqual(local_geometry(after)['status'],'available')
        self.assertEqual(after.geometry_revision,2)
        self.assertEqual(after.refresh_status,'delivery_unavailable')

    def test_withdrawal_removes_geometry_and_stale_retry_cannot_restore_it(self):
        self.accepted()
        self.ref.revision=3;self.ref.boundary_status='unavailable';self.ref.save()
        feature=self.publish({'status':'available','revision':'2','sha256':'a'*64,'geojson':GEO})
        self.assertIsNone(feature.geometry)
        self.assertEqual(feature.publication_status,'unavailable')
        self.assertEqual(feature.geometry_revision,3)

    def test_replacement_then_restoration_uses_new_accepted_revision(self):
        self.accepted()
        self.ref.revision=3;self.ref.boundary_status='unavailable';self.ref.save()
        self.publish({'status':'unavailable','revision':'3'})
        self.ref.revision=4;self.ref.boundary_status='available';self.ref.geometry_sha256='b'*64;self.ref.save()
        feature=self.publish({'status':'available','revision':'4','sha256':'b'*64,'geojson':GEO})
        self.assertIsNotNone(feature.geometry)
        self.assertEqual(feature.geometry_revision,4)
        self.assertEqual(GeographicFeature.objects.filter(source_instance='fixture').count(),1)

    def test_metadata_race_cannot_publish_different_revision_or_hash(self):
        before=self.accepted()
        for revision,sha in [('3','a'*64),('2','b'*64)]:
            after=self.publish({'status':'available','revision':revision,'sha256':sha,'geojson':GEO})
            self.assertEqual(after.geometry,before.geometry)
            self.assertEqual(after.geometry_revision,2)
            self.assertEqual(after.refresh_status,'metadata_changed_retry')

    def test_local_legacy_delivery_never_calls_msr(self):
        self.accepted()
        from msr_etl.geometry_access import load_geometry
        with patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden')):
            self.assertEqual(load_geometry(self.ref)['geojson'],GEO)

    def test_old_receipt_requires_restage_before_writing(self):
        from msr_etl.geo_synchronization import validate_receipt, SnapshotRejected
        unit=SimpleNamespace(raw_payload={'_mapping':{'version':1}},pk=1,job_uuid='old',source_type='mapping',unit_code='13')
        with self.assertRaisesRegex(SnapshotRejected, 'RESTAGE'):
            validate_receipt(unit)

    def test_coordinator_state_transfers_without_mapping_models(self):
        migration=importlib.import_module('msr_etl.migrations.0006_msrgeosyncstate_and_more')
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE mapping_sync_state (id bigserial primary key, source_instance varchar(100), district_msr_id bigint, last_applied_unit_id bigint, last_successful_sync timestamp)')
            cursor.execute('INSERT INTO mapping_sync_state (source_instance,district_msr_id,last_applied_unit_id) VALUES (%s,%s,%s)',['fixture',13,900])
        editor=SimpleNamespace(connection=connection)
        migration.adopt_ordering(apps,editor)
        state=MsrGeoSyncState.objects.get(source_instance='fixture')
        self.assertEqual(state.last_applied_unit_id,900)
        state.last_applied_unit_id=1000;state.save()
        migration.restore_ordering(apps,editor)
        with connection.cursor() as cursor:
            cursor.execute('SELECT last_applied_unit_id FROM mapping_sync_state WHERE source_instance=%s',['fixture'])
            self.assertEqual(cursor.fetchone()[0],1000)

    def test_independent_importer_installation_has_no_legacy_table_requirement(self):
        migration=importlib.import_module('msr_etl.migrations.0006_msrgeosyncstate_and_more')
        with patch.object(connection.introspection,'table_names',return_value=['msr_etl_geo_sync_state']):
            migration.adopt_ordering(apps,SimpleNamespace(connection=connection))
        self.assertFalse(MsrGeoSyncState.objects.exists())
