import json
import logging

from django.db import transaction

from individual.apps import IndividualConfig
from individual.data_source import DATA_SOURCE_FIELD, data_source_q
from individual.models import Individual, IndividualDataSource

from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_documents import link_documents
from msr_etl.kobo_review import compare, match_fields
from msr_etl.models import KoboSubmission
from msr_etl import signals
from msr_etl.sinks.individual_import_sink import IndividualImportSink

logger = logging.getLogger(__name__)

# Columns the individual import takes besides its schema's properties (individual workflows' header check)
_IMPORT_EXTRA_COLUMNS = {"recipient_info", "group_code", "individual_role", DATA_SOURCE_FIELD, "ID"}
APPLIED_STATUSES = (KoboSubmission.Status.IMPORTED, KoboSubmission.Status.UPDATED)


def _send_applied(submission, individual, status):
    """Tell receivers (e.g. appraisal) an application reached its Individual."""
    instance = KoboSubmission.objects.filter(**submission).first()
    if instance is not None:
        signals.send(signals.kobo_submission_applied, KoboSubmission, submission=instance, individual=individual,
                     status=status)


class KoboIndividualImportSink(IndividualImportSink):
    """
    Kobo applications into Individuals, matched on national ID within the source:
    - not in the MIS: a new Individual;
    - in the MIS with the same match fields (case and extra spaces ignored): the existing record is updated;
    - in the MIS with any match field different: held for review, the record is left as it is.
    The KoboSubmission of each application records the outcome and the Individual.
    """

    def __init__(self, user, source_type: str = None):
        super().__init__(user, source_type=source_type)
        config = MsrEtlConfig.get_source_config(self.source_type)
        self.asset_uid = str(config.get("asset_uid") or "")
        self.national_id_field = config.get("national_id_field") or "national_id"
        self.match_fields = match_fields(self.source_type)
        schema = json.loads(IndividualConfig.individual_schema or "{}")
        self.importable = (set(schema.get("properties", {})) | set(IndividualConfig.individual_base_fields or [])
                           | _IMPORT_EXTRA_COLUMNS) - {"id"}
        # A match field that is never stored would hold every repeat application
        unstored = [field for field in (*self.match_fields, self.national_id_field) if field not in self.importable]
        if unstored:
            raise self.Error(f"Kobo match fields are not in the individual schema: {', '.join(unstored)}")

    def push(self, data: list[dict], batch_identifier=None):
        # The import checks national IDs are unique within a file, and a repeat must be compared with the
        # record its first application created, so each round takes one application per national ID
        for number, records in enumerate(self._rounds(data or [])):
            self._push_round(records, f"{batch_identifier}_round{number}")

    def _rounds(self, data):
        rounds, seen = [], {}
        for record in sorted(data, key=lambda r: int(r.get("kobo_id") or 0)):
            national_id = record[self.national_id_field]
            position = seen.get(national_id, 0)
            seen[national_id] = position + 1
            if position == len(rounds):
                rounds.append([])
            rounds[position].append(record)
        return rounds

    def _push_round(self, records, batch_identifier):
        records = [self._scope_to_source(record) for record in records]
        existing = self._existing_individuals(records)
        new, updates, held = [], [], []
        for record in records:
            individual = existing.get(record[self.national_id_field])
            if individual is None:
                new.append(record)
                continue
            differences = self._differences(individual, record)
            if differences:
                held.append((record, individual, differences))
            else:
                updates.append({**record, "ID": individual.id})

        if new:
            result = self._import_new_records(self._importable(new), f"import_{batch_identifier}")
            self._mark_imported(new, result, KoboSubmission.Status.IMPORTED)
        if updates:
            result = self._update_existing_records(self._importable(updates), f"update_{batch_identifier}")
            self._mark_imported(updates, result, KoboSubmission.Status.UPDATED)
        for record, individual, differences in held:
            self._mark(record, KoboSubmission.Status.HELD, individual=individual,
                       reason="Differs from the existing record: " + "; ".join(differences))

    def _importable(self, records):
        """Only the columns the individual import accepts: the others are refused by its header check. Every
        answer stays in KoboSubmission.data; declare a field in the individual schema to import it."""
        left_out = sorted({key for record in records for key in record} - self.importable)
        if left_out:
            logger.info("Kobo answers not in the individual schema, kept on the submission only: %s", left_out)
        return [{key: value for key, value in record.items() if key in self.importable} for record in records]

    def _existing_individuals(self, records):
        national_ids = {record[self.national_id_field] for record in records}
        individuals = Individual.objects.filter(
            data_source_q(self.source_type), is_deleted=False,
            **{f"json_ext__{self.national_id_field}__in": list(national_ids)},
        ).order_by("date_created")
        found = {}
        for individual in individuals:
            found.setdefault((individual.json_ext or {}).get(self.national_id_field), individual)
        return found

    def _differences(self, individual, record):
        return [f"{row['field']} '{row['mis']}' in the MIS, '{row['applied']}' applied"
                for row in compare(individual, record, self.match_fields) if row["differs"]]

    def _mark_imported(self, records, result, status):
        """Link each application to its Individual, or record why the import refused it."""
        upload_uuid = ((result or {}).get("data") or {}).get("upload_uuid")
        errors = self._validation_errors(upload_uuid)
        individuals = self._existing_individuals(records)
        for record in records:
            refused = errors.get(record[self.national_id_field])
            individual = individuals.get(record[self.national_id_field])
            if refused or individual is None:
                self._mark(record, KoboSubmission.Status.INVALID,
                           reason=refused or "The individual import did not create the record")
            else:
                self._mark(record, status, individual=individual)

    def _validation_errors(self, upload_uuid):
        """{national ID: why the import refused that row} for one import; a national ID is once per round."""
        if not upload_uuid:
            return {}
        errors = {}
        for source in IndividualDataSource.objects.filter(upload_id=upload_uuid).only("json_ext", "validations"):
            problems = (source.validations or {}).get("validation_errors") or []
            if problems:
                national_id = str((source.json_ext or {}).get(self.national_id_field))
                errors[national_id] = "; ".join(f"{p.get('field_name')}: {p.get('note')}" for p in problems)
        return errors

    def _mark(self, record, status, individual=None, reason=""):
        submission = {"source_type": self.source_type, "asset_uid": self.asset_uid, "kobo_id": record.get("kobo_id")}
        KoboSubmission.objects.filter(**submission).update(status=status, individual=individual, reason=reason)
        # Its documents go with it: to the record it created or updated, or the one it was held from
        if individual is not None:
            link_documents(submission, individual)
        if status in APPLIED_STATUSES:
            transaction.on_commit(lambda: _send_applied(submission, individual, status))
