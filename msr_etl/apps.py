from django.apps import AppConfig

MODULE_NAME = "msr_etl"

DEFAULT_CONFIG = {
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
    "sources": {
        "ubr": {
            "auth_type": "noauth",  # noauth, basic, bearer
            "auth_basic_username": "",
            "auth_basic_password": "",
            "auth_bearer_token": "",
            "base_url": "",
            "households_endpoint_path": "/get_households_data",
            "geo_locations_endpoint_path": "/get_geo_locations",
            "headers": {},
            "timeout_seconds": 300,
            "retry_total": 3,
            "retry_backoff_factor": 1.0,
            "percentile_chunk_size": 10,
            "percentile_chunk_delay_seconds": 1.0,
            "verify_ssl": True,
            "ca_bundle_path": "",
            "programme_parameter_id": 2,
            "disability_parameter_id": 6,
        },
    },
}


class MsrEtlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

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

    sources = None

    @classmethod
    def _load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(MsrEtlConfig, field):
                setattr(MsrEtlConfig, field, cfg[field])

    @classmethod
    def get_source_config(cls, source_type):
        """Connection/auth/parsing config for a registered source_type, or {} if unconfigured."""
        return (cls.sources or {}).get(source_type, {})

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self._load_config(cfg)
