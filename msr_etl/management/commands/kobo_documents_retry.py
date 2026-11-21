from django.core.management.base import BaseCommand

from msr_etl.kobo_documents import requeue_failed


class Command(BaseCommand):
    help = ("Put applicant documents that used up their download attempts back in the queue, e.g. after Kobo "
            "was down or a size limit was raised. The next scheduled run downloads them.")

    def add_arguments(self, parser):
        parser.add_argument("--source", help="Only this Kobo source (default: every source)")

    def handle(self, *args, source=None, **options):
        count = requeue_failed(source)
        self.stdout.write(f"{count} documents put back in the queue")
