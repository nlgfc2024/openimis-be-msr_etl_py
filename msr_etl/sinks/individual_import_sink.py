import logging
from msr_etl.sinks import DataSink
from msr_etl.utils import data_to_file
from core.models import User
from individual.models import Individual, IndividualDataSourceUpload
from individual.services import IndividualImportService
from workflow.services import WorkflowService
from msr_etl.apps import MsrEtlConfig

logger = logging.getLogger(__name__)

IMPORT_NEW_INDIVIDUALS = "Python Import Individuals"
UPDATE_EXISTING_INDIVIDUALS = "Python Update Individuals"
WORKFLOW_GROUP = "individual"
GROUP_AGGREGATION_COLUMN = None


def resolve_import_user(user: User):
    username = MsrEtlConfig.sink_import_username
    if username:
        import_user = User.objects.filter(username=username).first()
        if not import_user:
            raise DataSink.Error(f"Configured sink_import_username not found: {username}")
        return ensure_import_user_login_name(import_user)

    return ensure_import_user_login_name(user)


def ensure_import_user_login_name(user: User):
    try:
        login_name = getattr(user, 'login_name')
    except AttributeError:
        login_name = None

    if login_name:
        return user

    username = getattr(user, 'username', None)
    if not username:
        raise AttributeError('User has no attribute login_name or username')

    setattr(user, 'login_name', username)
    return user


class IndividualImportSink(DataSink):

    def __init__(self, user: User):
        super().__init__()
        self.service = IndividualImportService(resolve_import_user(user))
        self.import_new_workflow = self.get_workflow(IMPORT_NEW_INDIVIDUALS)
        self.update_existing_workflow = self.get_workflow(UPDATE_EXISTING_INDIVIDUALS)

    def push(self, data: list[dict], batch_identifier=None):
        existing_records, new_records = self._split_existing_and_new(data)

        if new_records:
            self._import_new_records(new_records, batch_identifier)
        else:
            logger.debug("No new record to import")

        if existing_records:
            if MsrEtlConfig.sink_update_existing:
                self._update_existing_records(existing_records, batch_identifier)
            else:
                logger.debug(f"Skipped updating {len(existing_records)} existing records due to MsrEtlConfig.sink_update_existing = False")
        else:
            logger.debug("No existing record to update")

    def _import_new_records(self, new_records, batch_identifier):
        import_file = data_to_file(new_records, f'import_{batch_identifier}')
        result_new = self.service.import_individuals(
            import_file, self.import_new_workflow, GROUP_AGGREGATION_COLUMN
        )
        self._raise_if_upload_failed(result_new, 'import')
        logger.debug(f"Imported {len(new_records)} new records with {result_new}")

    def _update_existing_records(self, existing_records, batch_identifier):
        update_file = data_to_file(existing_records, f'update_{batch_identifier}')
        result_existing = self.service.import_individuals(
            update_file, self.update_existing_workflow, GROUP_AGGREGATION_COLUMN
        )
        self._raise_if_upload_failed(result_existing, 'update')
        logger.debug(f"Updated {len(existing_records)} existing records with {result_existing}")

    def _raise_if_upload_failed(self, result: dict, action: str):
        upload_uuid = (result or {}).get('data', {}).get('upload_uuid')
        if not upload_uuid:
            return

        upload = (
            IndividualDataSourceUpload.objects
            .filter(uuid=upload_uuid)
            .only('status', 'error')
            .first()
        )
        if not upload or upload.status != IndividualDataSourceUpload.Status.FAIL:
            return

        raise self.Error(f"Individual {action} workflow failed: {upload.error}")

    def _split_existing_and_new(self, data: list[dict]) -> tuple[list[dict], list[dict]]:
        model_lookup_field = MsrEtlConfig.sink_model_lookup_field

        data_ids = [self._get_data_id(record, model_lookup_field) for record in data]
        existing_data_id_to_db_id_map = self._get_existing_individual_ids(data_ids, model_lookup_field)

        existing_records = []
        new_records = []
        for record in data:
            data_id = self._get_data_id(record, model_lookup_field)
            if data_id in existing_data_id_to_db_id_map:
                record['ID'] = existing_data_id_to_db_id_map[data_id]
                existing_records.append(record)
            else:
                new_records.append(record)

        return existing_records, new_records

    def _get_existing_individual_ids(self, data_ids: list, model_lookup_field: str) -> dict:
        filter_kwargs = {f"{model_lookup_field}__in": data_ids}
        queryset = Individual.objects.filter(**filter_kwargs)

        results = queryset.values_list(model_lookup_field, 'id')
        return dict(results) if results else {}

    def _get_data_id(self, data: dict, key: str):
        # Supports any field on individual or a field on individual.json_ext
        keys = key.split('__')
        return data[keys[-1]]

    @staticmethod
    def get_workflow(name):
        result = WorkflowService.get_workflows(name, WORKFLOW_GROUP)
        if not result.get('success'):
            raise DataSink.Error(f"{result.get('message')}: {result.get('details')}")
        workflows = result.get('data', {}).get('workflows')
        if not workflows:
            raise DataSink.Error(f"Workflow not found: group={WORKFLOW_GROUP} name={name}")
        if len(workflows) > 1:
            raise DataSink.Error(f"Multiple workflows found: group={WORKFLOW_GROUP} name={name}")
        return workflows[0]
