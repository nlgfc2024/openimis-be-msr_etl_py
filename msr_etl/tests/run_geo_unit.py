import unittest
import django
from django.conf import settings
if not settings.configured:
    settings.configure(SECRET_KEY='geo-test', INSTALLED_APPS=[], DATABASES={'default': {'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}})
django.setup()
if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('msr_etl.tests.test_geo_provider'))
    raise SystemExit(not result.wasSuccessful())
