import os

from django.core.management.base import BaseCommand, CommandError

from msr_etl.kobo_location_choices import choice_files, push_location_choices


class Command(BaseCommand):
    help = ("Write the Kobo form's cities.csv, wards.csv and neighbourhoods.csv from the MIS locations, "
            "or push them into a Kobo source's form and redeploy it.")

    def add_arguments(self, parser):
        parser.add_argument("--out", help="Directory to write the CSV files to")
        parser.add_argument("--cities", nargs="*", help="Only these city codes (default: every city)")
        parser.add_argument("--push", metavar="SOURCE_TYPE",
                            help="Put the lists into this Kobo source's form instead (e.g. kobo)")

    def handle(self, *args, **options):
        if options["push"]:
            try:
                result = push_location_choices(options["push"])
            except ValueError as exc:
                raise CommandError(str(exc))
            self.stdout.write(f"Updated: {', '.join(result['updated']) or 'none'}; "
                              f"unchanged: {', '.join(result['unchanged']) or 'none'}; "
                              f"redeployed: {'yes' if result['redeployed'] else 'no'}")
            if result["warning"]:
                self.stderr.write(result["warning"])
            return

        out = options["out"] or "."
        if not os.path.isdir(out):
            raise CommandError(f"Not a directory: {out}")
        try:
            files = choice_files(options["cities"])
        except ValueError as exc:
            raise CommandError(str(exc))
        for name, content in files.items():
            path = os.path.join(out, name)
            with open(path, "wb") as handle:
                handle.write(content)
            rows = content.count(b"\n") - 1
            self.stdout.write(f"Wrote {rows} rows to {path}")
