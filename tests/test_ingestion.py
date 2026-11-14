"""Offline failure-ordering tests plus opt-in real PostgreSQL integration."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.telemetry.ingestion import decode_simulation, InvalidTelemetry, EventHandler
from airheater.storage.postgres_simulation import PostgresSimulationStore, PostgresCheckpointStore


def payload(**updates):
    data = dict(schema="airheater.simulation.v1", device_id="airheater-sim-1",
                sequence_number=110, timestamp="2026-11-14T10:39:17+00:00",
                elapsed_seconds=29, temperature_c=25.8, heater_voltage=2)
    data.update(updates)
    return json.dumps(data).encode()


def event(body=None):
    return Mock(body=[payload() if body is None else body],
                system_properties={b"iothub-connection-device-id": b"airheater-sim-1"},
                enqueued_time=datetime.now(timezone.utc))


class IngestionTests(unittest.TestCase):
    def test_valid_and_offset_normalized_to_utc(self):
        data = decode_simulation(payload(timestamp="2026-11-14T11:39:17+01:00"), "airheater-sim-1")
        self.assertEqual(data["timestamp"].hour, 10)
        self.assertEqual(data["timestamp"].tzinfo, timezone.utc)

    def test_invalid_messages(self):
        cases = [payload(schema="old"), payload(sequence_number=True), payload(sequence_number=0),
                 payload(sequence_number=2**63), payload(heater_voltage=5.1), payload(heater_voltage="2"),
                 payload(temperature_c=float("nan")), payload(temperature_c=float("inf")),
                 payload(elapsed_seconds=-1), payload(timestamp="2026-11-14"),
                 payload(timestamp="2099-01-01T00:00:00Z"), payload(device_id="other"),
                 b'{}', b'[]', b'\xff', b'x'*4097,
                 payload().replace(b'"sequence_number": 110', b'"sequence_number": 110, "sequence_number": 111')]
        for body in cases:
            with self.subTest(body=repr(body)[:80]), self.assertRaises(InvalidTelemetry):
                decode_simulation(body, "airheater-sim-1")

    def test_database_retry_blocks_checkpoint(self):
        calls = []
        def insert(*args):
            calls.append("insert")
            if calls.count("insert") == 1:
                raise OSError("secret must not be logged")
            calls.append("commit")
        store = Mock(insert=insert)
        context = Mock(update_checkpoint=lambda e: calls.append("checkpoint"))
        EventHandler(store, sleep=lambda _: calls.append("wait"))(context, event())
        self.assertEqual(calls, ["insert", "wait", "insert", "commit", "checkpoint"])

    def test_checkpoint_retry_does_not_reinsert(self):
        context, store = Mock(), Mock()
        context.update_checkpoint.side_effect = [OSError(), None]
        EventHandler(store, sleep=lambda _: None)(context, event())
        self.assertEqual(store.insert.call_count, 1)
        self.assertEqual(context.update_checkpoint.call_count, 2)

    def test_invalid_skipped_without_database_write(self):
        store, context = Mock(), Mock()
        EventHandler(store)(context, event(b'{}'))
        store.insert.assert_not_called()
        context.update_checkpoint.assert_called_once()

    def test_sql_is_bound_and_commit_precedes_return(self):
        connection = Mock()
        calls = []
        @contextmanager
        def connect():
            yield connection
            calls.append("commit")
        data = decode_simulation(payload(), "airheater-sim-1")
        data["device_id"] = "'; DROP TABLE simulation_measurements;--"
        PostgresSimulationStore(connect).insert(data, datetime.now(timezone.utc))
        sql, values = connection.execute.call_args.args
        self.assertNotIn(data["device_id"], sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertEqual(values[0], data["device_id"])
        self.assertEqual(calls, ["commit"])


@unittest.skipUnless(os.environ.get("AIRHEATER_TEST_POSTGRES_DSN"), "No disposable PostgreSQL test DSN")
class PostgresIntegrationTests(unittest.TestCase):
    def test_duplicate_and_checkpoint_roundtrip(self):
        import psycopg
        # Use a disposable initialized database. Transaction is rolled back so
        # this test never changes stored measurements or checkpoints.
        with psycopg.connect(os.environ["AIRHEATER_TEST_POSTGRES_DSN"]) as connection:
            @contextmanager
            def connect():
                yield connection
            try:
                data = decode_simulation(payload(device_id="integration-test", sequence_number=9223372036854775806), "integration-test")
                store = PostgresSimulationStore(connect)
                store.insert(data, datetime.now(timezone.utc))
                store.insert(data, datetime.now(timezone.utc))
                count = connection.execute("SELECT count(*) FROM simulation_measurements WHERE device_id=%s AND sequence_number=%s", (data["device_id"], data["sequence_number"])).fetchone()[0]
                self.assertEqual(count, 1)
                checkpoints = PostgresCheckpointStore(connect)
                cp = dict(fully_qualified_namespace="test", eventhub_name="test", consumer_group="test", partition_id="0", offset="42", sequence_number=42)
                checkpoints.update_checkpoint(cp)
                self.assertEqual(checkpoints.list_checkpoints("test", "test", "test")[0]["offset"], "42")
            finally:
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
