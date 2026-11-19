import json

from django.core.management.base import BaseCommand, CommandError

from msr_etl.apps import MsrEtlConfig
from msr_etl.kobo_form import form_fields, individual_schema, read_xlsform, store_form


class Command(BaseCommand):
    help = ("Store a Kobo source's form (from Kobo, or an exported XLSForm) and write the individual schema "
            "for its answers.")

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="The Kobo source_type, e.g. kobo")
        parser.add_argument("--xlsform", help="Read the form from this exported XLSForm instead of Kobo")
        parser.add_argument("--schema-out", help="Write the individual schema to this JSON file")
        parser.add_argument("--individual-config",
                            help="Set individual_schema in this individual module config file (e.g. coremis-configs)")

    def handle(self, *args, **options):
        source_type = options["source"]
        config = MsrEtlConfig.get_source_config(source_type)
        if config.get("connector") != "kobo":
            raise CommandError(f"'{source_type}' is not a Kobo source")
        asset_uid = str(config.get("asset_uid") or "")

        if options["xlsform"]:
            form = read_xlsform(options["xlsform"])
            store_form(source_type, asset_uid, form)
        else:
            from msr_etl.sources import KoboSubmissionSource

            stored = KoboSubmissionSource(source_type).sync_form()
            if stored is None:
                raise CommandError(f"Kobo form {asset_uid} has no deployed version")
            form = stored.form
        schema = individual_schema(form)
        self.stdout.write(f"Form {form['id_string'] or asset_uid} version {form['version']}: "
                          f"{len(form['sections'])} steps, {len(form_fields(form))} questions, "
                          f"{len(schema['properties'])} schema properties")

        if options["schema_out"]:
            with open(options["schema_out"], "w", encoding="utf-8") as handle:
                json.dump(schema, handle, indent=2)
                handle.write("\n")
            self.stdout.write(f"Wrote {options['schema_out']}")
        if options["individual_config"]:
            path = options["individual_config"]
            with open(path, encoding="utf-8") as handle:
                individual_config = json.load(handle)
            individual_config["individual_schema"] = json.dumps(schema)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(individual_config, handle, indent=2)
                handle.write("\n")
            self.stdout.write(f"Set individual_schema in {path}")
