import graphene

from api_etl.apps import ApiEtlConfig
from api_etl.gql_queries import (
    ETLServicesGQLType,
    ETLServicesListGQLType,
)
from api_etl.gql_mutations import ETLServiceMutation
from api_etl.utils import (
    get_class_by_name,
    get_classes_in_module,
    ETL_CLASS
)


class Query(graphene.ObjectType):

    etl_services_by_service_name = graphene.Field(
        ETLServicesListGQLType,
        name_of_service=graphene.Argument(graphene.String, required=False),
    )

    def resolve_etl_services_by_service_name(parent, info, **kwargs):
        if not info.context.user.has_perms(ApiEtlConfig.gql_query_api_etl_rule_perms):
            raise PermissionError("Unauthorized")

        list_sr = []
        service_name = kwargs.get("name_of_service", None)
        if service_name:
            # check if provided service etl class exists in application
            class_service = get_class_by_name(ETL_CLASS, service_name)
            if class_service:
                list_sr.append(
                    ETLServicesGQLType(
                        name_of_service=class_service.__name__,
                    )
                )
        else:
            # get all etl classes within module
            class_service_list = get_classes_in_module(ETL_CLASS)
            for class_service in class_service_list:
                list_sr.append(
                    ETLServicesGQLType(
                        name_of_service=class_service,
                    )
                )
        return ETLServicesListGQLType(list_sr)


class Mutation(graphene.ObjectType):
    etl_service_mutation = ETLServiceMutation.Field()
