from typing import Literal, Optional

from msr_etl.apps import MsrEtlConfig
from msr_etl.auth_provider.base import AuthError, AuthProvider
from msr_etl.auth_provider.basicAuthProvider import BasicAuthProvider
from msr_etl.auth_provider.bearerAuthProvider import BearerAuthProvider
from msr_etl.auth_provider.noAuthAuthProvider import NoAuthProvider

_auth_config_mapping = {
    "noauth": NoAuthProvider,
    "basic": BasicAuthProvider,
    "bearer": BearerAuthProvider,
}


def get_auth_provider(auth_type: Optional[Literal["noauth", "basic", "bearer"]] = None, source_type: str = "ubr"):
    auth_type = auth_type or MsrEtlConfig.get_source_config(source_type).get("auth_type", "noauth")
    if auth_type not in _auth_config_mapping:
        raise AuthError(f"Unknown auth type: {auth_type}")
    return _auth_config_mapping[auth_type](source_type=source_type)
