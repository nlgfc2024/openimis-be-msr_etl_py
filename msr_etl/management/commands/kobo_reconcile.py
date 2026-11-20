import json

from django.core.management.base import BaseCommand, CommandError

from msr_etl.kobo_reconcile import FIXES, GAPS, reconcile
from msr_etl.kobo_runs import kobo_source_types
from msr_etl.sources import KoboSubmissionSource

# Listed per gap; the rest are counted
_SHOWN = 50


class Command(BaseCommand):
    help = ("Check that the MIS holds every submission and file a Kobo form has, before Kobo is switched off. "
            "Read-only: lists the gaps and what fixes each. Exits with an error while there are gaps.")

    def add_arguments(self, parser):
        parser.add_argument("--source", help="Only this Kobo source (default: every Kobo source)")
        parser.add_argument("--checksums", action="store_true",
                            help="Also re-read every stored file and compare its sha256 (slower)")
        parser.add_argument("--json", dest="json_path", help="Also write the full report to this JSON file")

    def handle(self, *args, source=None, checksums=False, json_path=None, **options):
        sources = kobo_source_types()
        if source:
            if source not in sources:
                raise CommandError(f"'{source}' is not a Kobo source")
            sources = [source]
        if not sources:
            raise CommandError("No Kobo source is configured")

        reports = []
        for source_type in sources:
            try:
                report = reconcile(KoboSubmissionSource(source_type), checksums=checksums)
            except KoboSubmissionSource.Error as exc:
                raise CommandError(f"Could not read '{source_type}' from Kobo: {exc}") from exc
            reports.append(report)
            self._write(report, checksums)

        if json_path:
            with open(json_path, "w") as handle:
                json.dump(reports, handle, indent=2, default=str)
            self.stdout.write(f"Report written to {json_path}")
        incomplete = [report["source"] for report in reports if not report["complete"]]
        if incomplete:
            raise CommandError(f"Not complete: {', '.join(incomplete)}. Fix the gaps above before switching Kobo off.")

    def _write(self, report, checksums):
        out = self.stdout.write
        out(f"== {report['source']} (Kobo form {report['asset_uid']})")
        out(f"Submissions: {report['kobo_submissions']} in Kobo, {report['mis_submissions']} in the MIS")
        out("  " + ", ".join(f"{status} {count}" for status, count in report["outcomes"].items() if count))
        documents = ", ".join(f"{status} {count}" for status, count in report["documents"].items())
        out(f"Files: {report['kobo_attachments']} in Kobo; in the MIS {documents}")
        for gap in GAPS:
            items = report[gap]
            if not items:
                continue
            out(self.style.WARNING(f"{gap.replace('_', ' ')}: {len(items)}. {FIXES[gap]}"))
            for item in items[:_SHOWN]:
                out(f"  {item}")
            if len(items) > _SHOWN:
                out(f"  ... and {len(items) - _SHOWN} more (see --json)")
        if report["not_in_kobo"]:
            out(f"Kept in the MIS, no longer in Kobo: {len(report['not_in_kobo'])}")
        if report["complete"]:
            checked = "files present and checksums match" if checksums else "files present"
            out(self.style.SUCCESS(f"Complete: every submission and file is in the MIS ({checked})."))
