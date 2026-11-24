from django.test import TestCase

from msr_etl.location_file import current_location_code, former_location_codes
from msr_etl.models import MsrEtlLocationCodeChange


class LocationCodeHistoryTest(TestCase):
    def _change(self, old, new, location_type="R"):
        MsrEtlLocationCodeChange.objects.create(location_type=location_type, old_code=old, new_code=new, location_id=1)

    def test_former_codes_follow_a_chain_of_replacements(self):
        self._change("LL", "LL01")
        self._change("LL01", "206")
        self._change("LL", "W9", location_type="W")

        self.assertEqual(former_location_codes("R", "206"), {"LL01", "LL"})
        self.assertEqual(former_location_codes("R", "LL01"), {"LL"})
        self.assertEqual(former_location_codes("R", "325"), set())
        self.assertEqual(current_location_code("R", "LL"), "206")

    def test_enrolment_gets_the_history_when_installed(self):
        from enrolment.locations import location_codes

        self._change("MZ", "107")
        self.assertEqual(location_codes("R", "107"), ["107", "MZ"])
