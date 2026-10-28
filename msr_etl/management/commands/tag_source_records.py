from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from individual.models import GroupIndividual, Individual, IndividualDataSource
from msr_etl.models import MsrEtlSyncUnit
from msr_etl.sinks.individual_import_sink import DEFAULT_SOURCE_TYPE, SOURCE_TYPE_FIELD, source_group_code


def batch_file_names(units):
    """Upload names IndividualImportSink gives each unit's new/updated records."""
    return {f"{action}_job_{unit.job_uuid}_unit_{unit.id}.csv" for unit in units for action in ("import", "update")}


class Command(BaseCommand):
    help = (
        "Tag individuals and households imported from a non-default source before records carried "
        "their source_type, found through the source's import batches. Reports only unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", action="append", dest="sources",
                            help="Source to tag (repeatable). Default: every non-default source with import history.")
        parser.add_argument("--username", default="Admin", help="User recorded on the changes.")
        parser.add_argument("--apply", action="store_true", help="Write the changes (default: report only).")

    def handle(self, *args, sources=None, username=None, apply=False, **options):
        units = MsrEtlSyncUnit.objects.filter(kind=MsrEtlSyncUnit.Kind.INDIVIDUAL)
        sources = sources or sorted(set(units.exclude(source_type=DEFAULT_SOURCE_TYPE).values_list("source_type", flat=True)))
        if DEFAULT_SOURCE_TYPE in sources:
            raise CommandError(f"'{DEFAULT_SOURCE_TYPE}' is the default source; its untagged records need no tagging")

        for source in sources:
            counts = self._tag_source(source, units.filter(source_type=source), username, apply)
            self.stdout.write(f"{source}: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))
        if not apply:
            self.stdout.write("Report only; rerun with --apply to write.")

    def _tag_source(self, source, units, username, apply):
        counts = Counter()
        names = batch_file_names(units)
        individuals = Individual.objects.filter(
            individualdatasource__upload__source_name__in=names, is_deleted=False,
        ).distinct()

        tagged_ids = set()
        for individual in individuals:
            tag = (individual.json_ext or {}).get(SOURCE_TYPE_FIELD)
            if tag == source:
                counts["individuals already tagged"] += 1
                tagged_ids.add(individual.id)
                continue
            if tag:
                counts["individuals tagged with another source (skipped)"] += 1
                continue
            # Imported by another source's batches as well (e.g. matched an MSR record before tagging)
            if IndividualDataSource.objects.filter(individual=individual).exclude(upload__source_name__in=names).exists():
                counts["individuals shared with other imports (skipped)"] += 1
                continue
            counts["individuals tagged"] += 1
            tagged_ids.add(individual.id)
            if apply:
                individual.json_ext = {**(individual.json_ext or {}), SOURCE_TYPE_FIELD: source}
                individual.save(username=username)

        groups = {membership.group for membership in GroupIndividual.objects.filter(
            individual_id__in=tagged_ids, is_deleted=False,
        ).select_related("group")}
        for group in groups:
            member_ids = set(GroupIndividual.objects.filter(group=group, is_deleted=False).values_list("individual_id", flat=True))
            if not member_ids <= tagged_ids:
                counts["households with members from other sources (skipped)"] += 1
                continue
            counts["households tagged"] += 1
            if apply:
                group.code = source_group_code(source, group.code)
                group.json_ext = {**(group.json_ext or {}), SOURCE_TYPE_FIELD: source}
                group.save(username=username)
        return counts
