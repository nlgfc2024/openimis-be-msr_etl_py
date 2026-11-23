"""One-time host handover; configure MSR import and Mapping display separately."""
from copy import deepcopy
import json
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import ModuleConfiguration, AsyncJob
from location.models import MsrLocationReference
from msr_etl.apps import DEFAULT_CONFIG


class Command(BaseCommand):
    help = 'Preview/apply MSR configuration handover. No role grants or geographic links are changed.'

    def add_arguments(self, parser):
        parser.add_argument('--apply',action='store_true')

    @transaction.atomic
    def handle(self,*args,**options):
        if AsyncJob.objects.filter(module__in=['mapping','msr_etl']).exclude(status__in=AsyncJob.TERMINAL_STATUSES).exists():
            raise CommandError('Finish existing imports and stop old workers before ownership handover.')
        mapping = ModuleConfiguration.objects.select_for_update().filter(module='mapping',layer='be').first()
        msr = ModuleConfiguration.objects.select_for_update().filter(module='msr_etl',layer='be').first()
        if msr is None:
            raise CommandError('Configure the existing msr_etl backend first.')
        old = json.loads(mapping.config) if mapping else {}
        config = json.loads(msr.config)
        sources = config.setdefault('sources',{})
        for instance, alias in old.get('sources',{}).items():
            key = alias.get('legacy_source_type','msr_geo_v3')
            saved = {**DEFAULT_CONFIG['sources'].get(key,{}),**sources.get(key,{})}
            if saved.get('source_instance') not in (None,instance):
                raise CommandError('Source identity conflicts with existing MSR configuration.')
            overrides = {key:value for key,value in alias.items() if key not in ('legacy_source_type','provider')}
            sources[key] = {**saved,**overrides,'connector':'msr_geo_v3','kind':'location','source_instance':instance}
        for key, source in sources.items():
            if source.get('connector') == 'msr_geo_v3':
                source['filter_schema'] = deepcopy(DEFAULT_CONFIG['sources']['msr_geo_v3']['filter_schema'])
        if mapping and not old.get('permitted_district_uuids'):
            ids = old.get('permitted_district_ids',[])
            refs = MsrLocationReference.objects.filter(source_instance__in=old.get('permitted_source_instances',[]),
                msr_type='districts',msr_id__in=ids,location__type='R',location__validity_to__isnull=True)
            if ids and set(refs.values_list('msr_id',flat=True)) != set(ids):
                raise CommandError('Missing reviewed district links for scope transfer.')
            old['permitted_district_uuids'] = sorted(set(map(str,refs.values_list('location__uuid',flat=True))))
        for key in ('sources','sync_perms','permitted_district_ids'):
            old.pop(key,None)
        frontend = ModuleConfiguration.objects.select_for_update().filter(module='fe-mapping',layer='fe').first()
        fe = json.loads(frontend.config) if frontend else {}
        for key in ('sources','syncRights'):
            fe.pop(key,None)
        self.stdout.write('Plan: move MSR configuration to msr_etl; retain Mapping view scopes, existing credentials and role grants.')
        if not options['apply']:
            self.stdout.write('Preview only. Add --apply to save.')
            return
        msr.config=json.dumps(config); msr.is_exposed=False; msr.save()
        if mapping:
            mapping.config=json.dumps(old); mapping.is_exposed=False; mapping.save()
        if frontend:
            frontend.config=json.dumps(fe); frontend.save()
        self.stdout.write(self.style.SUCCESS('Ownership configuration transferred. Restart backend/workers and frontend.'))
