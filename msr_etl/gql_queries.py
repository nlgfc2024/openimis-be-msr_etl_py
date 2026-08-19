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


class MsrEtlSyncUnitGQLType(graphene.ObjectType):
    # raw_payload is intentionally never projected
    id = graphene.Int()
    unit_type = graphene.String()
    unit_code = graphene.String()
    stage_status = graphene.String()
    sync_status = graphene.String()
    record_count = graphene.Int()
    error_detail = graphene.String()
    attempts = graphene.Int()
    created_at = graphene.DateTime()
    updated_at = graphene.DateTime()


class MsrEtlSyncUnitsGQLType(graphene.ObjectType):
    units = graphene.List(MsrEtlSyncUnitGQLType)
    count = graphene.Int()
    total_count = graphene.Int()
