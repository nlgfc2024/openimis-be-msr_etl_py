from django.apps import AppConfig

MODULE_NAME = "msr_etl"

DEFAULT_CONFIG = {
    "auth_type": "noauth",  # noauth, basic, bearer
    "auth_basic_username": "",  # basic auth username
    "auth_basic_password": "",  # basic auth password
    "auth_bearer_token": "",  # bearer token

    "source_http_method": "",  # valid input for requests.request required
    "source_url": "",
    "source_headers": {},
    "source_batch_size": 50,
    "source_timeout_seconds": 300,
    "source_retry_total": 3,
    "source_retry_backoff_factor": 1.0,
    "source_percentile_chunk_size": 10,
    "source_percentile_chunk_delay_seconds": 1.0,
    "source_verify_ssl": True,
    "source_ca_bundle_path": "",

    "ubr_programme_parameter_id": 2,
    "ubr_disability_parameter_id": 6,

    "adapter_first_name_field": "firstName",
    "adapter_last_name_field": "lastName",
    "adapter_dob_field": "dateOfBirth",
    "adapter_location_name_field": "locationName",
    "adapter_location_code_field": "locationCode",

    "sink_model_lookup_field": "json_ext__ubr_id",
    "sink_update_existing": True,
    "sink_import_username": "",

    "gql_query_msr_etl_rule_perms": ["953001"],
    "gql_mutation_execute_msr_etl_rule_perms": ["953002"],

    "staging_retention_hours": 48,
    "sync_unit_max_attempts": 3,
    "sync_sweep_interval_minutes": 5,
    "sync_orphan_grace_minutes": 10,
    "job_stale_after_hours": 12,
}


class MsrEtlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

    auth_type = None
    auth_basic_username = None
    auth_basic_password = None
    auth_bearer_token = None

    source_http_method = None
    source_url = None
    source_headers = None
    source_batch_size = None
    source_timeout_seconds = None
    source_retry_total = None
    source_retry_backoff_factor = None
    source_percentile_chunk_size = None
    source_percentile_chunk_delay_seconds = None
    source_verify_ssl = None
    source_ca_bundle_path = None

    ubr_programme_parameter_id = None
    ubr_disability_parameter_id = None

    adapter_first_name_field = None
    adapter_last_name_field = None
    adapter_dob_field = None
    adapter_location_name_field = None
    adapter_location_code_field = None

    sink_model_lookup_field = None
    sink_update_existing = None
    sink_import_username = None

    gql_query_msr_etl_rule_perms = None
    gql_mutation_execute_msr_etl_rule_perms = None

    staging_retention_hours = None
    sync_unit_max_attempts = None
    sync_sweep_interval_minutes = None
    sync_orphan_grace_minutes = None
    job_stale_after_hours = None

    @classmethod
    def _load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(MsrEtlConfig, field):
                setattr(MsrEtlConfig, field, cfg[field])

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self._load_config(cfg)
