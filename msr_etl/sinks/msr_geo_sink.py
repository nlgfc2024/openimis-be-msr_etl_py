from django.db import transaction
from django.core.exceptions import ValidationError

from location.msr_reference import MsrReferenceWriter
from msr_etl.apps import MsrEtlConfig
from msr_etl.sinks.base import DataSink


class MsrGeoSink(DataSink):
    def __init__(self, user, source_type):
        self.writer = MsrReferenceWriter(MsrEtlConfig.get_source_config(source_type).get("source_instance"))

    @transaction.atomic
    def push(self, data, batch_identifier=None):
        for snapshot in data:
            if snapshot.get("source_instance") != self.writer.source_instance:
                raise ValidationError("Staged MSR instance differs from the configured source instance")
            self.writer.push(snapshot["locations"])
            self.writer.replace_memberships(snapshot["memberships"])
