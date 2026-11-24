"""Pure coordinate contract tests; no database or source credentials."""
from unittest import TestCase
from msr_etl.household_gps import household_gps, batch_metadata, GPS_COLUMN


class HouseholdGpsTests(TestCase):
    def test_current_fields_and_optional_quality(self):
        result = household_gps({'id': 17, 'gps_latitude':' -14.1 ', 'gps_longitude':'33.2',
                                'gps_accuracy':'4.5', 'gps_elevation':'-2',
                                'gps_latitude_new':1, 'gps_longitude_new':2})
        self.assertEqual((result['latitude'],result['longitude']),(-14.1,33.2))
        self.assertEqual(result['accuracy'],4.5)
        self.assertEqual(result['elevation'],-2)
        self.assertEqual(result['source_household_id'],'17')
        self.assertEqual(result['coordinate_type'],'household')

    def test_invalid_and_missing_pairs_are_omitted(self):
        for lat,lon in [(None,33),('',33),('nan',33),(-14,'Infinity'),(91,33),(-14,181),(True,33),('bad',33)]:
            with self.subTest(lat=lat,lon=lon):
                self.assertIsNone(household_gps({'gps_latitude':lat,'gps_longitude':lon}))
        self.assertIsNone(household_gps({'gps_latitude_new':-14,'gps_longitude_new':33,
                                      'village':{'village_latitude':-14,'village_longitude':33}}))

    def test_zero_is_numeric_and_quality_is_not_invented(self):
        result=household_gps({'gps_latitude':0,'gps_longitude':0,'gps_accuracy':-1,'gps_elevation':'nan'})
        self.assertEqual((result['latitude'],result['longitude']),(0,0))
        self.assertIsNone(result['accuracy'])
        self.assertIsNone(result['elevation'])

    def test_members_deduplicate_and_conflicts_fail(self):
        gps=household_gps({'gps_latitude':-14,'gps_longitude':33})
        rows=[{'group_code':'00017',GPS_COLUMN:gps}]*2
        self.assertEqual(list(batch_metadata(rows,'ubr')['households']),['00017'])
        with self.assertRaises(ValueError):
            batch_metadata(rows+[{'group_code':'00017',GPS_COLUMN:{**gps,'latitude':-15}}],'ubr')
        self.assertIsNone(batch_metadata([{'group_code':'00017',GPS_COLUMN:None}],'ubr'))
