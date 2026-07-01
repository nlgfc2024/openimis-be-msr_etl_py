import graphene

from msr_etl.apps import MsrEtlConfig
from msr_etl.gql_queries import (
    MsrEtlServiceGQLType,
    MsrEtlServicesListGQLType,
    MsrUbrIndividualsGQLType,
    MsrUbrLocationBatchGQLType,
    MsrUbrLocationsGQLType,
)
from msr_etl.gql_mutations import (
    MsrEtlServiceMutation,
    SaveMsrUbrIndividualsMutation,
    SaveMsrUbrLocationsMutation,
)
from msr_etl.sources import UBRIndividualSource, UBRLocationSource
from msr_etl.utils import (
    get_class_by_name,
    get_classes_in_module,
    MSR_ETL_CLASS
)


class Query(graphene.ObjectType):

    msr_etl_services_by_service_name = graphene.Field(
        MsrEtlServicesListGQLType,
        name_of_service=graphene.Argument(graphene.String, required=False),
    )

    msr_ubr_individuals = graphene.Field(
        MsrUbrIndividualsGQLType,
        district=graphene.Argument(graphene.String, required=False),
        ta=graphene.Argument(graphene.String, required=False),
        village=graphene.Argument(graphene.String, required=False),
        lower_percentile_category=graphene.Argument(graphene.Int, required=False),
        upper_percentile_category=graphene.Argument(graphene.Int, required=False),
        wealth_quintiles=graphene.Argument(graphene.List(graphene.Int), required=False),
        classification=graphene.Argument(graphene.List(graphene.Int), required=False),
        gender=graphene.Argument(graphene.String, required=False),
        minAge=graphene.Argument(graphene.Int, required=False),
        maxAge=graphene.Argument(graphene.Int, required=False),
        has_labour=graphene.Argument(graphene.Boolean, required=False),
        labour_constrained=graphene.Argument(graphene.Boolean, required=False),
        excluded_programme_codes=graphene.Argument(graphene.List(graphene.String), required=False),
        household_head_gender=graphene.Argument(graphene.Int, required=False),
    )

    msr_ubr_locations = graphene.Field(
        MsrUbrLocationsGQLType,
        district=graphene.Argument(graphene.String, required=False),
        ta=graphene.Argument(graphene.String, required=False),
        village=graphene.Argument(graphene.String, required=False),
    )

    def _resolve_etl_services(parent, info, **kwargs):
        if not info.context.user.has_perms(MsrEtlConfig.gql_query_msr_etl_rule_perms):
            raise PermissionError("Unauthorized")

        list_sr = []
        service_name = kwargs.get("name_of_service", None)
        if service_name:
            # check if provided service etl class exists in application
            class_service = get_class_by_name(MSR_ETL_CLASS, service_name)
            if class_service:
                list_sr.append(
                    MsrEtlServiceGQLType(
                        name_of_service=class_service.__name__,
                    )
                )
        else:
            # get all etl classes within module
            class_service_list = get_classes_in_module(MSR_ETL_CLASS)
            for class_service in class_service_list:
                list_sr.append(
                    MsrEtlServiceGQLType(
                        name_of_service=class_service,
                    )
                )
        return MsrEtlServicesListGQLType(list_sr)

    def resolve_msr_etl_services_by_service_name(parent, info, **kwargs):
        return Query._resolve_etl_services(parent, info, **kwargs)

    def resolve_msr_ubr_individuals(parent, info, **kwargs):
        if not info.context.user.has_perms(MsrEtlConfig.gql_query_msr_etl_rule_perms):
            raise PermissionError("Unauthorized")

        lower_percentile_category = kwargs.get("lower_percentile_category")
        upper_percentile_category = kwargs.get("upper_percentile_category")
        wealth_quintiles = kwargs.get("wealth_quintiles")
        classification = kwargs.get("classification")
        gender = kwargs.get("gender")
        min_age = kwargs.get("minAge")
        max_age = kwargs.get("maxAge")
        has_labour = kwargs.get("has_labour")
        labour_constrained = kwargs.get("labour_constrained")
        excluded_programme_codes = kwargs.get("excluded_programme_codes")
        household_head_gender = kwargs.get("household_head_gender")

        if lower_percentile_category is not None or upper_percentile_category is not None:
            lower = 0 if lower_percentile_category is None else lower_percentile_category
            upper = 100 if upper_percentile_category is None else upper_percentile_category
            pmt_percentile_range = range(lower, upper + 1)
        else:
            pmt_percentile_range = range(0, 11)

        source = UBRIndividualSource(
            district=kwargs.get("district"),
            ta=kwargs.get("ta"),
            village=kwargs.get("village"),
            pmt_percentile_range=pmt_percentile_range,
            wealth_quintiles=wealth_quintiles,
            classification=classification,
            gender=gender,
            min_age=min_age,
            max_age=max_age,
            has_labour=has_labour,
            labour_constrained=labour_constrained,
            excluded_programme_codes=excluded_programme_codes,
            household_head_gender=household_head_gender,
        )
        individuals = source.fetch()
        return MsrUbrIndividualsGQLType(
            individuals=individuals,
            count=len(individuals),
            district=kwargs.get("district"),
            ta=kwargs.get("ta"),
            village=kwargs.get("village"),
        )

    def resolve_msr_ubr_locations(parent, info, **kwargs):
        if not info.context.user.has_perms(MsrEtlConfig.gql_query_msr_etl_rule_perms):
            raise PermissionError("Unauthorized")

        source = UBRLocationSource()
        raw_batches = source.fetch(
            district=kwargs.get("district"),
            ta=kwargs.get("ta"),
            village=kwargs.get("village"),
        )
        batches = [
            MsrUbrLocationBatchGQLType(
                data_type=batch.get("data_type"),
                locations=batch.get("data", []),
                count=len(batch.get("data", [])),
            )
            for batch in raw_batches
        ]
        return MsrUbrLocationsGQLType(
            batches=batches,
            count=sum(batch.count for batch in batches),
        )


class Mutation(graphene.ObjectType):
    execute_msr_etl_service = MsrEtlServiceMutation.Field()
    save_msr_ubr_individuals = SaveMsrUbrIndividualsMutation.Field()
    save_msr_ubr_locations = SaveMsrUbrLocationsMutation.Field()
