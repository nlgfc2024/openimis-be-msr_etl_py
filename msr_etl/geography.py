"""Translate accepted MSR references into CoreMIS's provider-neutral geography."""
from django.db import transaction
from django.utils import timezone
from location.models import GeographicFeature, GeographicMembership, MsrLocationReference, MsrCatchmentVillage
from location.geography import safe_geojson

KINDS = {"districts": "district", "traditional_authorities": "traditional_authority",
         "group_village_heads": "group_village_head", "villages": "village", "catchments": "micro_catchment"}


@transaction.atomic
def publish_snapshot(instance, rows, memberships, geometries=()):
    delivered = {(item['type'], item['id']): item['result'] for item in geometries}
    refs = list(MsrLocationReference.objects.filter(source_instance=instance))
    district_ids = {ref.msr_id: ref.location_id for ref in refs if ref.msr_type == 'districts'}
    accepted = {}
    keys = {(row['type'], row['id']) for row in rows}
    for ref in refs:
        if (ref.msr_type, ref.msr_id) not in keys:
            continue
        defaults = dict(kind=KINDS.get(ref.msr_type, ref.msr_type), name=ref.name, code=ref.code,
            district_id=district_ids.get(ref.district_msr_id), location_id=ref.location_id,
            micro_catchment_id=ref.micro_catchment_id, latitude=ref.latitude, longitude=ref.longitude,
            point_status=ref.point_status, observed_revision=ref.revision, synced_at=ref.synced_at,
            parent_keys=[{'type': p['type'], 'id': str(p['id'])} for p in ref.parents])
        if ref.msr_type == 'catchments' and not ref.is_micro:
            defaults['kind'] = 'catchment'
        feature, _ = GeographicFeature.objects.select_for_update().get_or_create(
            source_instance=instance, external_type=ref.msr_type, external_id=str(ref.msr_id), defaults=defaults)
        for key, value in defaults.items():
            setattr(feature, key, value)
        result = delivered.get((ref.msr_type, ref.msr_id))
        if ref.boundary_status == 'unavailable' and ref.revision >= (feature.geometry_revision or 0):
            feature.geometry = None
            feature.geometry_revision = ref.revision
            feature.geometry_hash = None
            feature.publication_status = 'unavailable'
            feature.refresh_status = 'unavailable'
            feature.geometry_relation, feature.geometry_source = ref.geometry_relation, ref.geometry_source
        elif result is not None:
            feature.refresh_status = 'metadata_changed_retry' if result['status'] == 'unavailable' else result['status']
            if (result['status'] == 'available' and int(result['revision']) == ref.revision
                    and result.get('sha256') == ref.geometry_sha256
                    and ref.revision >= (feature.geometry_revision or 0)):
                feature.geometry = safe_geojson(result['geojson'])
                feature.geometry_revision = ref.revision
                feature.geometry_hash = result['sha256']
                feature.geometry_relation, feature.geometry_source = result.get('relation'), result.get('source')
                feature.publication_status = 'available'
                feature.geometry_updated_at = timezone.now()
            elif result['status'] == 'available':
                feature.refresh_status = 'metadata_changed_retry'
            if feature.geometry is None:
                feature.publication_status = 'delivery_unavailable'
        feature.save()
        accepted[(ref.msr_type, ref.msr_id)] = feature
    # Mirror only writer-accepted membership state; preserve its revision checks.
    for snapshot in memberships:
        parent = accepted.get(('catchments', snapshot['catchment_id']))
        if parent is None:
            continue
        links = MsrCatchmentVillage.objects.filter(catchment__source_instance=instance,
            catchment__msr_id=snapshot['catchment_id']).select_related('village')
        children = []
        for link in links:
            child = accepted.get(('villages', link.village.msr_id)) or GeographicFeature.objects.get(
                source_instance=instance, external_type='villages', external_id=str(link.village.msr_id))
            children.append(child)
        GeographicMembership.objects.filter(parent=parent).delete()
        GeographicMembership.objects.filter(child__in=children).delete()
        GeographicMembership.objects.bulk_create([GeographicMembership(parent=parent, child=child, verified_at=timezone.now()) for child in children])
