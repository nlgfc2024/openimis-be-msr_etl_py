from unittest import TestCase
from unittest.mock import patch, MagicMock
from django.db import transaction
from core.models import User
from individual.models import Group, GroupDataSource, IndividualDataSourceUpload
from msr_etl.household_gps import household_gps, HOUSEHOLD_KEY, UPLOAD_KEY, GPS_COLUMN
from msr_etl.sinks.household_gps_import import HouseholdGpsImportService
from msr_etl.sinks.individual_import_sink import IndividualImportSink


class HouseholdGpsHostTests(TestCase):
    def setUp(self):
        self.atomic=transaction.atomic(); self.atomic.__enter__(); self.addCleanup(self.rollback)
        self.user=User.objects.filter(i_user__isnull=False).first()
        self.group=Group(code='00017',json_ext={'data_source':'ubr','members':['preserved'],'custom':'preserved'})
        self.group.save(user=self.user)
        self.gps=household_gps({'id':17,'gps_latitude':'-14.1','gps_longitude':'33.2','gps_accuracy':5})

    def rollback(self):
        transaction.set_rollback(True); self.atomic.__exit__(None,None,None)

    def upload(self,gps=None,source='ubr'):
        value=IndividualDataSourceUpload(source_name='gps-test',source_type='csv',
            json_ext={UPLOAD_KEY:{'source_type':source,'households':{'00017':gps or self.gps}}})
        value.save(user=self.user)
        return value

    def accepted(self,upload,group=None):
        source=GroupDataSource(upload=upload,group=group or self.group)
        source.save(user=self.user)
        self.group.refresh_from_db()
        return source

    def test_only_approved_household_receives_metadata_and_replay_is_idempotent(self):
        upload=self.upload()
        proposal=GroupDataSource(upload=upload); proposal.save(user=self.user)
        self.group.refresh_from_db()
        self.assertNotIn(HOUSEHOLD_KEY,self.group.json_ext)
        proposal.group=self.group; proposal.save(user=self.user)
        self.group.refresh_from_db()
        version=self.group.version
        self.assertEqual(self.group.json_ext[HOUSEHOLD_KEY]['latitude'],-14.1)
        self.assertEqual(self.group.json_ext['members'],['preserved'])
        self.assertEqual(self.group.json_ext['custom'],'preserved')
        proposal.save(user=self.user)
        self.group.refresh_from_db()
        self.assertEqual(self.group.version,version)
        self.assertEqual(Group.objects.count(),1)

    def test_missing_metadata_and_other_sources_cannot_replace_gps(self):
        self.accepted(self.upload())
        before=self.group.json_ext.copy()
        upload=IndividualDataSourceUpload(source_name='no-gps',source_type='csv'); upload.save(user=self.user)
        self.accepted(upload)
        self.accepted(self.upload(source='other-source'))
        failed=self.upload()
        failed.status=IndividualDataSourceUpload.Status.FAIL; failed.save(user=self.user)
        self.accepted(failed)
        self.assertEqual(self.group.json_ext,before)

    def test_newer_valid_pair_updates_but_delayed_older_upload_does_not(self):
        old=self.upload()
        newer=self.upload({**self.gps,'latitude':-15})
        self.accepted(newer)
        self.accepted(old)
        self.assertEqual(self.group.json_ext[HOUSEHOLD_KEY]['latitude'],-15)
        # Even an unchanged later observation advances the ordering watermark.
        newest=self.upload({**self.gps,'latitude':-15})
        self.accepted(newest)
        self.assertEqual(self.group.json_ext[HOUSEHOLD_KEY]['imported_at'],newest.date_created.isoformat())

    def test_service_stages_gps_before_host_workflow_record_creation(self):
        service=HouseholdGpsImportService(self.user,{'source_type':'ubr','households':{'00017':self.gps}})
        upload=IndividualDataSourceUpload(source_name='gps-test',source_type='csv');upload.save(user=self.user)
        def check(*args):
            upload.refresh_from_db()
            self.assertIn(UPLOAD_KEY,upload.json_ext)
        with patch('individual.services.IndividualImportService._create_individual_data_upload_records',side_effect=check) as parent:
            service._create_individual_data_upload_records(MagicMock(),upload,None)
        parent.assert_called_once()

    def test_actual_upload_service_carries_gps_without_member_columns(self):
        from individual.models import IndividualDataSource
        from msr_etl.sinks.individual_import_sink import ensure_import_user_login_name
        from msr_etl.utils import data_to_file
        user=ensure_import_user_login_name(self.user)
        service=HouseholdGpsImportService(user,{'source_type':'ubr','households':{'00017':self.gps}})
        workflow=MagicMock(name='fixture workflow')
        workflow.name='GPS fixture'
        def approve(arguments):
            upload=IndividualDataSourceUpload.objects.get(pk=arguments['upload_uuid'])
            self.assertIn(UPLOAD_KEY,upload.json_ext)
            self.assertNotIn(GPS_COLUMN,IndividualDataSource.objects.get(upload=upload).json_ext)
            self.accepted(upload)
            return {'success':True}
        workflow.run.side_effect=approve
        result=service.import_individuals(data_to_file([{'first_name':'Fixture','last_name':'Only',
            'dob':'1990-01-01','group_code':'00017'}],'gps-fixture'),workflow,None)
        self.assertTrue(result['success'])
        workflow.run.assert_called_once()
        self.assertEqual(self.group.json_ext[HOUSEHOLD_KEY]['longitude'],33.2)

    def test_legacy_household_credentials_fallback_and_explicit_override(self):
        from msr_etl.apps import MsrEtlConfig
        old=MsrEtlConfig._legacy_ubr_config
        try:
            with patch.object(MsrEtlConfig,'sources',{}):
                MsrEtlConfig._legacy_ubr_config={'auth_type':'bearer','auth_bearer_token':'fixture-only'}
                self.assertEqual(MsrEtlConfig.get_source_config('ubr')['auth_bearer_token'],'fixture-only')
                self.assertEqual(MsrEtlConfig.get_source_config('msr_geo_v3')['auth_bearer_token'],'')
            with patch.object(MsrEtlConfig,'sources',{'ubr':{'auth_type':'noauth','auth_bearer_token':''}}):
                self.assertEqual(MsrEtlConfig.get_source_config('ubr')['auth_bearer_token'],'')
                self.assertEqual(MsrEtlConfig.get_source_config('ubr')['auth_type'],'noauth')
        finally:
            MsrEtlConfig._legacy_ubr_config=old

    def test_adapter_emits_household_only_metadata_for_each_member(self):
        from msr_etl.adapters.ubr_adapter import UBRIndividualAdapter
        row={'id':17,'form_number':'00017','pmt_score':'1','gps_latitude':'-14.1',
             'gps_longitude':'33.2','gps_latitude_new':0,'gps_longitude_new':0,
             'household_members':[{'id':n,'first_name':'Zelda','last_name':'Fixture',
                 'date_of_birth':'1990-01-01'} for n in (1,2)]}
        data=UBRIndividualAdapter().transform([row])
        self.assertEqual(len(data),2)
        self.assertEqual(data[0][GPS_COLUMN],data[1][GPS_COLUMN])
        self.assertEqual(data[0][GPS_COLUMN]['latitude'],-14.1)
        self.assertNotIn('gps_latitude',data[0])

    def test_failed_workflow_does_not_write_household_gps(self):
        from msr_etl.sinks.individual_import_sink import ensure_import_user_login_name
        from msr_etl.utils import data_to_file
        service=HouseholdGpsImportService(ensure_import_user_login_name(self.user),
            {'source_type':'ubr','households':{'00017':self.gps}})
        workflow=MagicMock();workflow.name='Failed fixture'
        workflow.run.side_effect=ValueError('fixture failure')
        result=service.import_individuals(data_to_file([{'first_name':'Fixture','last_name':'Only',
            'dob':'1990-01-01','group_code':'00017'}],'gps-failed-fixture'),workflow,None)
        upload=IndividualDataSourceUpload.objects.get(pk=result['data']['upload_uuid'])
        self.assertEqual(upload.status,IndividualDataSourceUpload.Status.FAIL)
        self.group.refresh_from_db()
        self.assertNotIn(HOUSEHOLD_KEY,self.group.json_ext)

    def test_sink_strips_household_payload_from_member_csv_and_skip_has_no_write(self):
        sink=IndividualImportSink.__new__(IndividualImportSink)
        sink.source_type='ubr'; sink.import_user=self.user; sink.service=MagicMock()
        service,rows=sink._household_service([{'group_code':'00017','ubr_id':1,GPS_COLUMN:self.gps}])
        self.assertIsInstance(service,HouseholdGpsImportService)
        self.assertNotIn(GPS_COLUMN,rows[0])
        self.assertNotIn('latitude',rows[0])
        self.assertEqual(service.household_gps_metadata['households']['00017'],self.gps)
        self.group.refresh_from_db()
        self.assertNotIn(HOUSEHOLD_KEY,self.group.json_ext)
