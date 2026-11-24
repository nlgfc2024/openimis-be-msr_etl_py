"""Reuse the host import service, keeping household-only metadata out of CSV."""
from individual.services import IndividualImportService
from msr_etl.household_gps import UPLOAD_KEY


class HouseholdGpsImportService(IndividualImportService):
    def __init__(self, user, metadata):
        super().__init__(user)
        self.household_gps_metadata = metadata

    def _create_individual_data_upload_records(self, workflow, upload, group_aggregation_column):
        upload.json_ext = {**(upload.json_ext or {}), UPLOAD_KEY: self.household_gps_metadata}
        upload.save(user=self.user)
        return super()._create_individual_data_upload_records(workflow, upload, group_aggregation_column)
