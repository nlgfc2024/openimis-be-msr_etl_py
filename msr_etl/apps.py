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
            "connector": "msr_api",
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
            "filter_schema": {
                "individual": [
                    {"name": "location", "label": "Location", "type": "location", "required": True},
                    {
                        "name": "wealth_quintiles", "label": "Wealth Quintile / Classification",
                        "type": "multiselect",
                        "options": [
                            {"value": 1, "label": "Poorest"},
                            {"value": 2, "label": "Poorer"},
                            {"value": 3, "label": "Poor"},
                            {"value": 4, "label": "Better Off"},
                            {"value": 5, "label": "Rich"},
                        ],
                    },
                    {"name": "lower_percentile_category", "label": "Lower Percentile Category",
                     "type": "number", "min": 0, "max": 100},
                    {"name": "upper_percentile_category", "label": "Upper Percentile Category",
                     "type": "number", "min": 0, "max": 100},
                    {
                        "name": "gender", "label": "Gender", "type": "select",
                        "options": [{"value": "Male", "label": "Male"}, {"value": "Female", "label": "Female"}],
                    },
                    {"name": "minAge", "label": "Min Age", "type": "number", "min": 0},
                    {"name": "maxAge", "label": "Max Age", "type": "number", "min": 0},
                    {"name": "has_labour", "label": "Household Has Labour", "type": "boolean"},
                    {"name": "labour_constrained", "label": "Household Is Labour Constrained", "type": "boolean"},
                    {
                        "name": "household_head_gender", "label": "Household Head Gender", "type": "select",
                        "options": [{"value": 1, "label": "Male-headed"}, {"value": 2, "label": "Female-headed"}],
                    },
                    {
                        "name": "excluded_programme_codes", "label": "Exclude Programmes", "type": "multiselect",
                        "options": [
                            {"value": "1", "label": "Social Cash Transfer"},
                            {"value": "2", "label": "Public Works Programme"},
                            {"value": "3", "label": "VSL/COMSIP"},
                            {"value": "4", "label": "Microfinance"},
                        ],
                    },
                ],
                "location": [
                    {"name": "location", "label": "Location", "type": "location", "required": True, "maxLevel": 3},
                ],
            },
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
        for field in cfg:
            if hasattr(MsrEtlConfig, field):
                setattr(MsrEtlConfig, field, cfg[field])

    @classmethod
    def get_source_config(cls, source_type):
        """
        Config for a source_type: saved keys over that source's DEFAULT_CONFIG entry, since ModuleConfiguration replaces the whole "sources" key
        """
        defaults = DEFAULT_CONFIG["sources"].get(source_type) or {}
        saved = (cls.sources or {}).get(source_type) or {}
        return {**defaults, **saved}

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self._load_config(cfg)
