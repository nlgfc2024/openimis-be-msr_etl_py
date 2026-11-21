import graphene
from django.contrib.auth.models import AnonymousUser
from django.db.models import Count, Q

from msr_etl.apps import MsrEtlConfig
from msr_etl.gql_queries import (
    ApplicantDocumentGQLType,
    KoboApplicationGQLType,
    KoboSubmissionGQLType,
    KoboSubmissionsGQLType,
    MsrEtlServiceGQLType,
    MsrEtlServicesListGQLType,
    MsrEtlSourceTypeGQLType,
    MsrEtlSourceTypesGQLType,
    MsrEtlSyncUnitsGQLType,
    MsrUbrIndividualsGQLType,
    MsrUbrLocationBatchGQLType,
    MsrUbrLocationsGQLType,
)
from msr_etl.kobo_documents import TABLE_MIMETYPES
from msr_etl.kobo_form import application_sections, form_fields, form_for_submission, raw_answers
from msr_etl.models import ApplicantDocument, KoboSubmission, MsrEtlSyncUnit
from msr_etl.gql_mutations import (
    MsrEtlServiceMutation,
    SaveMsrUbrIndividualsMutation,
    SaveMsrUbrLocationsMutation,
    ScheduleMsrUbrIndividualsImportMutation,
    ScheduleMsrUbrLocationsImportMutation,
)
from msr_etl.source_registry import (
    DEFAULT_SOURCE_TYPE, list_individual_source_types, list_location_source_types, source_enabled,
)
from msr_etl.sources import UBRIndividualSource, UBRLocationSource
from msr_etl.utils import (
    get_class_by_name,
    get_classes_in_module,
    MSR_ETL_CLASS
)


def _source_type_options(source_types, kind):
    def build(source_type):
        config = MsrEtlConfig.get_source_config(source_type)
        return MsrEtlSourceTypeGQLType(
            value=source_type,
            label=config.get("display_name") or None,
            source_name=config.get("source_name") or config.get("display_name") or None,
            filter_schema=(config.get("filter_schema") or {}).get(kind) or [],
        )

    return [build(source_type) for source_type in source_types]


class Query(graphene.ObjectType):

    msr_etl_services_by_service_name = graphene.Field(
        MsrEtlServicesListGQLType,
        name_of_service=graphene.Argument(graphene.String, required=False),
    )

    msr_ubr_individuals = graphene.Field(
        MsrUbrIndividualsGQLType,
        district=graphene.Argument(graphene.String, required=True),
        ta=graphene.Argument(graphene.String, required=True),
        gvh=graphene.Argument(graphene.String, required=False),
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
        gvh=graphene.Argument(graphene.String, required=False),
        village=graphene.Argument(graphene.String, required=False),
    )

    msr_etl_source_types = graphene.Field(MsrEtlSourceTypesGQLType)

    kobo_applications = graphene.List(
        KoboApplicationGQLType,
        individual_id=graphene.Argument(graphene.UUID, required=True),
    )

    kobo_submissions = graphene.Field(
        KoboSubmissionsGQLType,
        source_type=graphene.String(),
        status=graphene.String(),
        search=graphene.String(),
        first=graphene.Int(default_value=20),
        offset=graphene.Int(default_value=0),
    )

    applicant_documents = graphene.List(
        ApplicantDocumentGQLType,
        individual_id=graphene.Argument(graphene.UUID, required=True),
    )

    msr_etl_sync_units = graphene.Field(
        MsrEtlSyncUnitsGQLType,
        job_uuid=graphene.Argument(graphene.String, required=True),
        unit_type=graphene.Argument(graphene.String, required=False),
        sync_status=graphene.Argument(graphene.String, required=False),
        limit=graphene.Argument(graphene.Int, required=False, default_value=100),
        offset=graphene.Argument(graphene.Int, required=False, default_value=0),
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
        if not source_enabled():
            raise ValueError(f"The '{DEFAULT_SOURCE_TYPE}' source is turned off on this instance")

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
            gvh=kwargs.get("gvh"),
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
        if not source_enabled():
            raise ValueError(f"The '{DEFAULT_SOURCE_TYPE}' source is turned off on this instance")

        source = UBRLocationSource()
        raw_batches = source.fetch(
            district=kwargs.get("district"),
            ta=kwargs.get("ta"),
            gvh=kwargs.get("gvh"),
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

    @staticmethod
    def _visible_individual(info, individual_id):
        # The same right and location limits as reading the individual itself
        from individual.apps import IndividualConfig
        from individual.gql_queries import IndividualGQLType

        user = info.context.user
        if type(user) is AnonymousUser or not user.has_perms(IndividualConfig.gql_individual_search_perms):
            raise PermissionError("Unauthorized")
        return IndividualGQLType.get_queryset(None, info).filter(id=individual_id).exists()

    def resolve_applicant_documents(parent, info, individual_id, **kwargs):
        if not Query._visible_individual(info, individual_id):
            return []
        rights = MsrEtlConfig.applicant_document_perms or []
        can_open = bool(rights) and info.context.user.has_perms(rights)
        documents, labels = [], {}
        for document in ApplicantDocument.objects.filter(individual_id=individual_id) \
                .select_related("submission").order_by("-submission__kobo_id", "question"):
            submission = document.submission
            if submission.id not in labels:
                form = form_for_submission(submission)
                labels[submission.id] = {f["name"]: f["label"] for f in form_fields(form)} if form else {}
            stored = document.status == ApplicantDocument.Status.STORED
            url = f"msr_etl/individuals/{individual_id}/documents/{document.id}/" if can_open and stored else None
            documents.append(ApplicantDocumentGQLType(
                id=str(document.id), kobo_id=submission.kobo_id, document_type=document.document_type,
                label=labels[submission.id].get(document.document_type) or document.document_type,
                filename=document.filename, mimetype=document.mimetype, size=document.size, status=document.status,
                stored_at=document.stored_at, can_open=can_open and stored,
                url=url, table_url=f"{url}table/" if url and document.mimetype in TABLE_MIMETYPES else None,
            ))
        return documents

    def resolve_kobo_submissions(parent, info, source_type=None, status=None, search=None, first=20, offset=0,
                                 **kwargs):
        from msr_etl import kobo_review
        from msr_etl.kobo_runs import kobo_source_types

        user = info.context.user
        rights = MsrEtlConfig.kobo_submission_perms or []
        if type(user) is AnonymousUser or not rights or not user.has_perms(rights):
            raise PermissionError("Unauthorized")
        sources = kobo_source_types()
        if source_type:
            sources = [source for source in sources if source == source_type]
        submissions = kobo_review.search(kobo_review.visible_submissions(user, sources), search)
        filtered = submissions.filter(status=status) if status else submissions
        first, offset = min(max(int(first or 20), 1), 100), max(int(offset or 0), 0)
        # id breaks ties between sources sharing Kobo ids, so pages don't skip or repeat rows
        page = filtered.select_related("individual").annotate(
            document_count=Count("documents"),
            stored_count=Count("documents", filter=Q(documents__status=ApplicantDocument.Status.STORED)),
        ).order_by("-kobo_id", "id")[offset:offset + first]
        items, labels = [], {}
        for submission in page:
            answers = kobo_review.answers(submission)
            rows = kobo_review.comparison(submission)
            if rows:
                key = (submission.source_type, submission.asset_uid, (submission.data or {}).get("__version__"))
                if key not in labels:
                    form = form_for_submission(submission)
                    labels[key] = {f["name"]: f["label"] for f in form_fields(form)} if form else {}
                rows = [{**row, "label": labels[key].get(row["field"]) or row["field"]} for row in rows]
            items.append(KoboSubmissionGQLType(
                id=str(submission.id), source_type=submission.source_type, kobo_id=submission.kobo_id,
                submitted_at=submission.submitted_at, status=submission.status, reason=submission.reason,
                national_id=submission.national_id, first_name=str(answers.get("first_name") or ""),
                last_name=str(answers.get("last_name") or ""),
                individual_id=str(submission.individual_id) if submission.individual_id else None,
                comparison=rows, documents=submission.document_count, documents_stored=submission.stored_count,
            ))
        return KoboSubmissionsGQLType(items=items, total_count=filtered.count(),
                                      summary=kobo_review.summary(submissions))

    def resolve_kobo_applications(parent, info, individual_id, **kwargs):
        if not Query._visible_individual(info, individual_id):
            return []
        applications = []
        for submission in KoboSubmission.objects.filter(individual_id=individual_id).order_by("-kobo_id"):
            form = form_for_submission(submission)
            applications.append(KoboApplicationGQLType(
                id=str(submission.id), kobo_id=submission.kobo_id, status=submission.status,
                reason=submission.reason, submitted_at=submission.submitted_at,
                form_version=(form or {}).get("version") or "",
                sections=application_sections(submission, form) if form else raw_answers(submission),
            ))
        return applications

    def resolve_msr_etl_source_types(parent, info, **kwargs):
        if not info.context.user.has_perms(MsrEtlConfig.gql_query_msr_etl_rule_perms):
            raise PermissionError("Unauthorized")

        return MsrEtlSourceTypesGQLType(
            individual_source_types=_source_type_options(list_individual_source_types(), "individual"),
            location_source_types=_source_type_options(list_location_source_types(), "location"),
        )

    def resolve_msr_etl_sync_units(parent, info, **kwargs):
        if not info.context.user.has_perms(MsrEtlConfig.gql_query_msr_etl_rule_perms):
            raise PermissionError("Unauthorized")

        qs = MsrEtlSyncUnit.objects.filter(job_uuid=kwargs.get("job_uuid"))
        if kwargs.get("unit_type"):
            qs = qs.filter(unit_type=kwargs["unit_type"])
        if kwargs.get("sync_status"):
            qs = qs.filter(sync_status=kwargs["sync_status"])

        total_count = qs.count()
        limit = kwargs.get("limit") or 100
        offset = kwargs.get("offset") or 0
        units = list(qs.order_by("unit_code", "id")[offset:offset + limit])

        return MsrEtlSyncUnitsGQLType(
            units=units,
            count=len(units),
            total_count=total_count,
        )


class Mutation(graphene.ObjectType):
    execute_msr_etl_service = MsrEtlServiceMutation.Field()
    schedule_msr_ubr_individuals_import = ScheduleMsrUbrIndividualsImportMutation.Field()
    save_msr_ubr_individuals = SaveMsrUbrIndividualsMutation.Field()
    save_msr_ubr_locations = SaveMsrUbrLocationsMutation.Field()
    schedule_msr_ubr_locations_import = ScheduleMsrUbrLocationsImportMutation.Field()
