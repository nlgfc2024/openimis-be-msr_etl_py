"""Run household import regression checks in disposable PostgreSQL tables."""
import unittest
import uuid
from unittest.mock import patch
from django.db import connection


def run():
    from core.models import User
    from individual.models import (Group, Individual, GroupIndividual, GroupDataSource,
        IndividualDataSourceUpload, IndividualDataSource, IndividualDataUploadRecords)
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise RuntimeError('Run on PostgreSQL outside a transaction')
    actor=User.objects.filter(i_user__isnull=False).first()
    name='household_gps_test_'+uuid.uuid4().hex
    quoted=connection.ops.quote_name(name)
    with connection.cursor() as cursor:
        cursor.execute('SHOW search_path'); original=cursor.fetchone()[0]
        cursor.execute('CREATE SCHEMA '+quoted)
        cursor.execute('SET search_path TO '+quoted+', public')
        for model in (Group,Individual,GroupIndividual,GroupDataSource,IndividualDataSourceUpload,IndividualDataSource,IndividualDataUploadRecords):
            table=connection.ops.quote_name(model._meta.db_table)
            # No foreign keys to public household tables: every fixture stays isolated.
            cursor.execute('CREATE TABLE '+quoted+'.'+table+' (LIKE public.'+table+' INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)')
    try:
        labels=('msr_etl.tests.test_household_gps','msr_etl.tests.test_household_gps_host',
                'msr_etl.tests.test_ubr_adapter','msr_etl.tests.test_individual_import_sink',
                'msr_etl.tests.test_apps_config','msr_etl.tests.test_auth_provider_defaults')
        suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(label) for label in labels)
        with patch('core.test_helpers.LogInHelper.get_or_create_user_api',return_value=actor), \
             patch('django_opensearch_dsl.registries.registry.update'), \
             patch('django_opensearch_dsl.registries.registry.update_related'):
            result=unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError('Household GPS checks failed')
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('search_path',%s,false)",[original])
            cursor.execute('DROP SCHEMA '+quoted+' CASCADE')
