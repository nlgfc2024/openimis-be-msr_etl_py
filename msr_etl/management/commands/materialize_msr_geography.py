"""Publish already imported references into local, provider-neutral geography."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import AsyncJob
from location.models import MsrLocationReference
from msr_etl.apps import MsrEtlConfig
from msr_etl.models import MsrGeoSyncState
from msr_etl.geography import publish_snapshot
from msr_etl.geometry_access import download_geometry


class Command(BaseCommand):
    help = 'Materialize existing MSR references/memberships locally; optionally retrieve published geometry. No location/project relinking.'

    def add_arguments(self, parser):
        parser.add_argument('--source-type', default='msr_geo_v3')
        parser.add_argument('--district-id', type=int, required=True)
        parser.add_argument('--download', action='store_true')

    def handle(self, *args, **options):
        if AsyncJob.objects.filter(module__in=['mapping','msr_etl']).exclude(status__in=AsyncJob.TERMINAL_STATUSES).exists():
            raise CommandError('Finish existing imports and stop old workers before materializing geography.')
        config = MsrEtlConfig.get_source_config(options['source_type'])
        instance = config.get('source_instance')
        if not instance or options['district_id'] <= 0:
            raise CommandError('A configured source_instance and positive district ID are required.')
        refs = list(MsrLocationReference.objects.filter(source_instance=instance,district_msr_id=options['district_id']))
        if not refs:
            raise CommandError('No imported references found in this source/district.')
        geometries = []
        if options['download']:
            if config.get('enabled') is not True:
                raise CommandError('Enable the backend MSR source before downloading geometry.')
            for ref in refs:
                if ref.boundary_status == 'available':
                    geometries.append({'type':ref.msr_type,'id':ref.msr_id,'result':download_geometry(ref)})
        with transaction.atomic():
            state,_ = MsrGeoSyncState.objects.get_or_create(source_instance=instance,district_msr_id=options['district_id'])
            MsrGeoSyncState.objects.select_for_update().get(pk=state.pk)
            publish_snapshot(instance,[{'type':ref.msr_type,'id':ref.msr_id} for ref in refs],
                [{'catchment_id':ref.msr_id} for ref in refs if ref.msr_type=='catchments' and ref.is_micro],geometries)
        self.stdout.write(self.style.SUCCESS('Published %s local geographic features. Geometry downloads: %s; accepted: %s.' % (
            len(refs),len(geometries),sum(item['result']['status']=='available' for item in geometries))))
