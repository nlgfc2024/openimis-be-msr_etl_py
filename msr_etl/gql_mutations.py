import inspect

import graphene as graphene
from graphene.types.generic import GenericScalar

from django.utils.translation import gettext as _
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from msr_etl.adapters import UBRIndividualAdapter, UBRLocationAdapter
from msr_etl.utils import (
    get_class_by_name,
    get_timestamped_batch_identifier,
    MSR_ETL_CLASS,
)
from msr_etl.apps import MsrEtlConfig
from core.gql.gql_mutations.base_mutation import BaseMutation
from core.schema import OpenIMISMutation
from msr_etl.sinks import IndividualImportSink, LocationImportSink
from msr_etl.tasks.sync_job import register_ubr_location_initial_pull_job
from msr_etl.services import UBRIndividualService
from msr_etl.sources import UBRLocationSource


class MsrEtlServiceMutation(BaseMutation):
    """
    Mutation to execute the ETLService
    """
    _mutation_class = "MsrEtlServiceMutation"
    _mutation_module = "msr_etl"

    class Input(OpenIMISMutation.Input):
        name_of_service = graphene.String(required=True)
        district = graphene.String(required=False)
        ta = graphene.String(required=False)
        gvh = graphene.String(required=False)
        village = graphene.String(required=False)
        lower_percentile_category = graphene.Int(required=False)
        upper_percentile_category = graphene.Int(required=False)
        wealth_quintiles = graphene.List(graphene.Int, required=False)
        classification = graphene.List(graphene.Int, required=False)
        gender = graphene.String(required=False)
        minAge = graphene.Int(required=False)
        maxAge = graphene.Int(required=False)
        has_labour = graphene.Boolean(required=False)
        labour_constrained = graphene.Boolean(required=False)
        excluded_programme_codes = graphene.List(graphene.String, required=False)
        household_head_gender = graphene.Int(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        try:
            data.pop('client_mutation_id', None)
            data.pop('client_mutation_label', None)
            name_of_service = data.pop('name_of_service', None)
            if not name_of_service:
                return [{
                    'message': "msr_etl.mutation.failed_to_execute_etl_service",
                    'detail': _('There is no ETL service with provided name')
                }]

            etl_service_class = get_class_by_name(MSR_ETL_CLASS, name_of_service)
            service_kwargs = cls._get_supported_service_kwargs(etl_service_class, data)

            # Instantiate and execute the ETL service
            etl_service = etl_service_class(user, **service_kwargs)
            result = etl_service.execute()

            if result['success']:
                return None
            else:
                return [{
                    'message': result['message'],
                    'detail': result['detail']
                }]
        except Exception as exc:
            return [{
                'message': "msr_etl.mutation.failed_to_execute_etl_service",
                'detail': str(exc)
            }]

    @staticmethod
    def _get_supported_service_kwargs(etl_service_class, data):
        service_signature = inspect.signature(etl_service_class.__init__)
        supported_params = service_signature.parameters
        return {
            key: value
            for key, value in data.items()
            if value is not None and key in supported_params
        }


class ExecuteMsrUbrIndividualsImportMutation(BaseMutation):
    """
    Mutation to fetch filtered UBR household records and submit them directly
    into the openIMIS Individual import workflow.
    """
    _mutation_class = "ExecuteMsrUbrIndividualsImportMutation"
    _mutation_module = "msr_etl"

    class Input(OpenIMISMutation.Input):
        district = graphene.String(required=True)
        ta = graphene.String(required=True)
        gvh = graphene.String(required=False)
        village = graphene.String(required=False)
        lower_percentile_category = graphene.Int(required=False)
        upper_percentile_category = graphene.Int(required=False)
        wealth_quintiles = graphene.List(graphene.Int, required=False)
        classification = graphene.List(graphene.Int, required=False)
        gender = graphene.String(required=False)
        minAge = graphene.Int(required=False)
        maxAge = graphene.Int(required=False)
        has_labour = graphene.Boolean(required=False)
        labour_constrained = graphene.Boolean(required=False)
        excluded_programme_codes = graphene.List(graphene.String, required=False)
        household_head_gender = graphene.Int(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        try:
            data.pop('client_mutation_id', None)
            data.pop('client_mutation_label', None)
            service_kwargs = MsrEtlServiceMutation._get_supported_service_kwargs(
                UBRIndividualService,
                data,
            )
            result = UBRIndividualService(user, **service_kwargs).execute()

            if result['success']:
                return None

            return [{
                'message': result.get(
                    'message',
                    "msr_etl.mutation.failed_to_execute_ubr_individuals_import",
                ),
                'detail': result.get('detail'),
            }]
        except Exception as exc:
            return [{
                'message': "msr_etl.mutation.failed_to_execute_ubr_individuals_import",
                'detail': str(exc),
            }]


class SaveMsrUbrIndividualsMutation(BaseMutation):
    """
    Mutation to save selected UBR household records into the openIMIS Individual module.
    """
    _mutation_class = "SaveMsrUbrIndividualsMutation"
    _mutation_module = "msr_etl"

    class Input(OpenIMISMutation.Input):
        individuals = graphene.List(GenericScalar, required=True)
        batch_identifier = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        cls._validate_user(user)
        if not data.get("individuals"):
            raise ValidationError("msr_etl.mutation.individuals_required")

    @classmethod
    def _mutate(cls, user, **data):
        try:
            individuals = data.get("individuals") or []
            batch_identifier = data.get("batch_identifier") or get_timestamped_batch_identifier("msr_individuals_")
            transformed_individuals = UBRIndividualAdapter().transform(individuals)
            IndividualImportSink(user).push(transformed_individuals, batch_identifier)
            return None
        except Exception as exc:
            return [{
                'message': "msr_etl.mutation.failed_to_save_individuals",
                'detail': str(exc),
            }]

    @staticmethod
    def _validate_user(user):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms):
            raise ValidationError("mutation.authentication_required")


class SaveMsrUbrLocationsMutation(BaseMutation):
    """
    Mutation to save selected UBR geo-location batches into the openIMIS Location module.
    """
    _mutation_class = "SaveMsrUbrLocationsMutation"
    _mutation_module = "msr_etl"

    class Input(OpenIMISMutation.Input):
        location_batches = graphene.List(GenericScalar, required=True)
        batch_identifier = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        cls._validate_user(user)
        if not data.get("location_batches"):
            raise ValidationError("msr_etl.mutation.location_batches_required")

    @classmethod
    def _mutate(cls, user, **data):
        try:
            location_batches = data.get("location_batches") or []
            batch_identifier = data.get("batch_identifier") or get_timestamped_batch_identifier("msr_locations_")
            adapter = UBRLocationAdapter()
            sink = LocationImportSink(user)

            for batch in location_batches:
                normalized_batch = cls._normalize_location_batch(batch)
                transformed_locations = adapter.transform(normalized_batch)
                sink.push(transformed_locations, batch_identifier)
            return None
        except Exception as exc:
            return [{
                'message': "msr_etl.mutation.failed_to_save_locations",
                'detail': str(exc),
            }]

    @staticmethod
    def _normalize_location_batch(batch):
        return {
            "data_type": batch.get("data_type") or batch.get("dataType"),
            "data": batch.get("data") or batch.get("locations") or [],
        }

    @staticmethod
    def _validate_user(user):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms):
            raise ValidationError("mutation.authentication_required")


class ScheduleMsrUbrLocationInitialPullMutation(BaseMutation):
    """Registers a one-off background job that performs a full UBR location pull."""

    _mutation_class = "ScheduleMsrUbrLocationInitialPullMutation"
    _mutation_module = "msr_etl"

    class Input(OpenIMISMutation.Input):
        request_id = graphene.String(required=True)

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        try:
            request_id = data.get("request_id")
            register_ubr_location_initial_pull_job(user.id, request_id=request_id)
            return None
        except Exception as exc:
            return [{
                'message': "msr_etl.mutation.failed_to_schedule_location_initial_pull",
                'detail': str(exc),
            }]
