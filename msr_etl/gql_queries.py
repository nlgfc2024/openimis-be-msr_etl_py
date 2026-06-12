import graphene
from graphene.types.generic import GenericScalar


class MsrEtlServiceGQLType(graphene.ObjectType):
    name_of_service = graphene.String()


class MsrEtlServicesListGQLType(graphene.ObjectType):
    etl_services = graphene.List(MsrEtlServiceGQLType)


class MsrUbrIndividualsGQLType(graphene.ObjectType):
    individuals = graphene.List(GenericScalar)
    count = graphene.Int()
    district = graphene.String()
    ta = graphene.String()
    village = graphene.String()


class MsrUbrLocationBatchGQLType(graphene.ObjectType):
    data_type = graphene.String()
    locations = graphene.List(GenericScalar)
    count = graphene.Int()


class MsrUbrLocationsGQLType(graphene.ObjectType):
    batches = graphene.List(MsrUbrLocationBatchGQLType)
    count = graphene.Int()
