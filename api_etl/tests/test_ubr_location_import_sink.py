from django.test import TestCase
from unittest.mock import patch, MagicMock
from api_etl.sinks.location_import_sink import LocationImportSink
from location.models import Location
from core.test_helpers import LogInHelper


class TestLocationImportSink(TestCase):

    def setUp(self):
        self.user = LogInHelper().get_or_create_user_api()
        self.sink = LocationImportSink(self.user)

        # Mock existing locations in the database
        self.existing_location = Location.objects.create(
            code="101",
            name="Chitipa",
            type="D",
        )

        self.new_location_data = [
            {"code": "102", "name": "Karonga", "type": "D"},
            {"code": "103", "name": "Mzuzu", "type": "D"},
        ]

        self.update_location_data = [
            {"code": "101", "name": "Chitipa Updated", "type": "D"},
        ]

    @patch("location.models.Location.objects.bulk_create")
    @patch("location.models.Location.objects.bulk_update")
    def test_push_creates_and_updates_locations(self, mock_bulk_update, mock_bulk_create):
        data = self.new_location_data + self.update_location_data

        self.sink.push(data)

        # Assert bulk_create was called with new locations
        self.assertEqual(mock_bulk_create.call_count, 1)
        created_locations = mock_bulk_create.call_args[0][0]
        self.assertEqual(len(created_locations), 2)
        self.assertEqual(created_locations[0].code, "102")
        self.assertEqual(created_locations[1].code, "103")

        # Assert bulk_update was called with updated locations
        self.assertEqual(mock_bulk_update.call_count, 1)
        updated_locations = list(mock_bulk_update.call_args[0][0])
        self.assertEqual(len(updated_locations), 1)
        self.assertEqual(updated_locations[0].code, "101")
        self.assertEqual(updated_locations[0].name, "Chitipa Updated")

    @patch("location.models.Location.objects.bulk_create")
    @patch("location.models.Location.objects.bulk_update")
    def test_push_no_data(self, mock_bulk_update, mock_bulk_create):
        self.sink.push([])

        # Assert no bulk_create or bulk_update calls were made
        mock_bulk_create.assert_not_called()
        mock_bulk_update.assert_not_called()

    @patch("location.models.Location.objects.bulk_create")
    @patch("location.models.Location.objects.bulk_update")
    def test_push_only_new_locations(self, mock_bulk_update, mock_bulk_create):
        self.sink.push(self.new_location_data)

        # Assert bulk_create was called with new locations
        self.assertEqual(mock_bulk_create.call_count, 1)
        created_locations = mock_bulk_create.call_args[0][0]
        self.assertEqual(len(created_locations), 2)
        self.assertEqual(created_locations[0].code, "102")
        self.assertEqual(created_locations[1].code, "103")

        # Assert bulk_update was not called
        mock_bulk_update.assert_not_called()

    @patch("location.models.Location.objects.bulk_create")
    @patch("location.models.Location.objects.bulk_update")
    def test_push_only_update_locations(self, mock_bulk_update, mock_bulk_create):
        self.sink.push(self.update_location_data)

        # Assert bulk_update was called with updated locations
        self.assertEqual(mock_bulk_update.call_count, 1)
        updated_locations = list(mock_bulk_update.call_args[0][0])
        self.assertEqual(len(updated_locations), 1)
        self.assertEqual(updated_locations[0].code, "101")
        self.assertEqual(updated_locations[0].name, "Chitipa Updated")

        # Assert bulk_create was not called
        mock_bulk_create.assert_not_called()

    @patch("location.models.Location.objects.filter")
    def test_split_existing_and_new(self, mock_filter):
        mock_filter.return_value.values_list.return_value = [self.existing_location.code]

        data = self.new_location_data + self.update_location_data
        new_records, update_records = self.sink._split_existing_and_new(data)

        # Assert new records are correctly identified
        self.assertEqual(len(new_records), 2)
        self.assertEqual(new_records[0]["code"], "102")
        self.assertEqual(new_records[1]["code"], "103")

        # Assert update records are correctly identified
        self.assertEqual(len(update_records), 1)
        self.assertEqual(update_records[0]["code"], "101")

    @patch("location.models.Location.objects.bulk_create")
    def test_bulk_create_locations(self, mock_bulk_create):
        self.sink._bulk_create_locations(self.new_location_data)

        # Assert bulk_create was called with new locations
        self.assertEqual(mock_bulk_create.call_count, 1)
        created_locations = list(mock_bulk_create.call_args[0][0])
        self.assertEqual(len(created_locations), 2)
        self.assertEqual(created_locations[0].code, "102")
        self.assertEqual(created_locations[1].code, "103")

    @patch("location.models.Location.objects.bulk_update")
    def test_bulk_update_locations(self, mock_bulk_update):
        self.sink._bulk_update_locations(self.update_location_data)

        # Assert bulk_update was called with updated locations
        self.assertEqual(mock_bulk_update.call_count, 1)
        updated_locations = list(mock_bulk_update.call_args[0][0])
        self.assertEqual(len(updated_locations), 1)
        self.assertEqual(updated_locations[0].code, "101")
        self.assertEqual(updated_locations[0].name, "Chitipa Updated")