"""MSR import configuration; contains no Mapping dependency."""
from copy import deepcopy
import json
import os
from django.utils.crypto import salted_hmac


def configuration_fingerprint(config, policy="msr_etl"):
    from msr_etl.apps import MsrEtlConfig
    data = {"protocol": "msr-etl-geo-v2", "source": deepcopy(config), "policy": policy,
            "rights": MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms}
    if config.get("token_env"):
        data["credential_version"] = os.environ.get(config["token_env"], "")
    return salted_hmac("msr_etl.geo-config", json.dumps(data, sort_keys=True), algorithm="sha256").hexdigest()
