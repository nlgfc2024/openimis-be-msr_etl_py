import graphene


class ETLServicesGQLType(graphene.ObjectType):
    name_of_service = graphene.String()


class ETLServicesListGQLType(graphene.ObjectType):
    etl_services = graphene.List(ETLServicesGQLType)
