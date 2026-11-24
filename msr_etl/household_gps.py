"""Household GPS retention through the host's existing upload/approval lifecycle."""
import math
from decimal import Decimal, InvalidOperation

GPS_COLUMN = '_msr_household_gps'
UPLOAD_KEY = 'msr_household_gps'
HOUSEHOLD_KEY = 'household_gps'


def number(value):
    if value is None or isinstance(value, bool) or str(value).strip() == '':
        return None
    try:
        result = float(Decimal(str(value).strip()))
        return result if math.isfinite(result) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def household_gps(row):
    """Use the published current fields, never infer from villages or *_new."""
    lat, lon = number(row.get('gps_latitude')), number(row.get('gps_longitude'))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    accuracy = number(row.get('gps_accuracy'))
    return {'latitude': lat, 'longitude': lon,
            'accuracy': accuracy if accuracy is not None and accuracy >= 0 else None,
            'elevation': number(row.get('gps_elevation')),
            'source_household_id': str(row['id']) if row.get('id') is not None else None,
            'coordinate_type': 'household', 'source_fields': 'gps_latitude,gps_longitude'}


def batch_metadata(records, source_type):
    households = {}
    for record in records:
        gps, code = record.get(GPS_COLUMN), record.get('group_code')
        if gps is None or not code:
            continue
        code = str(code)
        if code in households and households[code] != gps:
            raise ValueError('Conflicting household GPS within an import batch')
        households[code] = gps
    return {'source_type': source_type, 'households': households} if households else None


def approved_household_gps(sender, instance, raw=False, **kwargs):
    """A GroupDataSource gains a group only when CoreMIS accepts that household."""
    if raw or instance.is_deleted or not instance.group_id or not instance.upload_id:
        return
    from django.db import transaction
    from individual.data_source import data_source_q
    from individual.models import Group, IndividualDataSourceUpload

    upload = IndividualDataSourceUpload.objects.get(pk=instance.upload_id)
    if upload.is_deleted or upload.status == IndividualDataSourceUpload.Status.FAIL:
        return
    metadata = (upload.json_ext or {}).get(UPLOAD_KEY)
    if not isinstance(metadata, dict):
        return
    source = metadata.get('source_type')
    if not source:
        return
    with transaction.atomic():
        group = Group.objects.select_for_update().filter(
            data_source_q(source), pk=instance.group_id, is_deleted=False).first()
        if group is None:
            return
        gps = metadata.get('households', {}).get(group.code)
        if not gps:
            return
        ext = dict(group.json_ext or {})
        previous = ext.get(HOUSEHOLD_KEY)
        previous = previous if isinstance(previous, dict) else {}
        observed = upload.date_created.isoformat()
        # Older concurrent uploads must not replace a more recently accepted pair.
        from django.utils.dateparse import parse_datetime
        stored_time = previous.get('imported_at')
        old_time = parse_datetime(stored_time) if isinstance(stored_time, str) else None
        if old_time and old_time > upload.date_created:
            return
        value = {**gps, 'source_type': source}
        value['imported_at'] = observed
        if previous == value:
            return
        ext[HOUSEHOLD_KEY] = value
        group.json_ext = ext
        group.save(user=instance.user_updated or upload.user_updated)


def connect_signals():
    from django.db.models.signals import post_save
    from individual.models import GroupDataSource
    post_save.connect(approved_household_gps, sender=GroupDataSource,
                      dispatch_uid='msr_etl.approved_household_gps', weak=False)
