from django.test import SimpleTestCase

from msr_etl.services.base import MsrETLService


class FakeSource:
    def pull(self):
        yield [{"id": 1}, {"id": 2}], "batch_one"
        yield [{"id": 3}], "batch_two"


class FakeAdapter:
    def transform(self, data):
        return [{"source_id": row["id"]} for row in data]


class FakeSink:
    def __init__(self):
        self.pushed = []

    def push(self, data, batch_identifier):
        self.pushed.append((data, batch_identifier))


class MsrETLServiceTestCase(SimpleTestCase):

    def test_execute_returns_batch_and_record_summary(self):
        sink = FakeSink()
        service = MsrETLService(
            source=FakeSource(),
            adapter=FakeAdapter(),
            sink=sink,
        )

        result = service.execute()

        self.assertTrue(result["success"])
        self.assertEqual(result["data"], {
            "batches_processed": 2,
            "source_records": 3,
            "transformed_records": 3,
            "batch_identifiers": ["batch_one", "batch_two"],
        })
        self.assertEqual(len(sink.pushed), 2)