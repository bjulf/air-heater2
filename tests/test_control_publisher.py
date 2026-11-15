"""Control-to-ingestion acceptance tests, including stalled transport."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('control_publisher',
    Path(__file__).resolve().parents[1]/'apps'/'control_publisher.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
from airheater.telemetry.ingestion import decode_simulation, InvalidTelemetry
from airheater.storage.postgres_simulation import PostgresSimulationStore


def payload():
    return dict(schema='airheater.control.v1', device_id='control-test',
        sequence_number=1, timestamp=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=1, temperature_c=28.1, heater_voltage=2,
        setpoint_c=28)


class ControlIngestionTests(unittest.TestCase):
    def decode(self, data):
        return decode_simulation(json.dumps(data), 'control-test')

    def test_control_schema_and_bound_insert(self):
        data = self.decode(payload())
        connection = Mock()
        @contextmanager
        def connect():
            yield connection
        PostgresSimulationStore(connect).insert(data, datetime.now(timezone.utc))
        sql, values = connection.execute.call_args.args
        self.assertIn('setpoint_c', sql)
        self.assertEqual(sql.count('%s'), len(values))
        self.assertEqual(values[7], 28)

    def test_missing_migration_detected_before_receiving(self):
        connection = Mock()
        @contextmanager
        def connect():
            yield connection
        store = PostgresSimulationStore(connect)
        connection.execute.return_value.fetchall.return_value = [('temperature_c',)]
        self.assertFalse(store.control_schema_ready())
        names = set(payload()) - {'schema'}
        connection.execute.return_value.fetchall.return_value = [(x,) for x in names]
        self.assertTrue(store.control_schema_ready())

    def test_rejects_missing_extra_and_invalid_control_values(self):
        cases = []
        for name in payload():
            data = payload()
            del data[name]
            cases.append(data)
        for field, value in [('setpoint_c', True), ('setpoint_c', 151),
                             ('setpoint_c', float('nan')), ('setpoint_c', float('inf')),
                             ('filtered_temperature_c', 28), ('kp', .52), ('extra', 1), ('schema', 'airheater.control.v2')]:
            data = payload()
            data[field] = value
            cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(InvalidTelemetry):
                self.decode(data)


class ControlPublisherTests(unittest.TestCase):
    def test_real_control_samples_reach_ingestion_and_sequence_survives(self):
        async def scenario(directory):
            sent = []
            attempts = []
            async def send(body):
                attempts.append(body)
                if len(attempts) == 1:
                    raise OSError('simulated transport failure')
                sent.append(body)
            args = app.parse_args(['--device-id', 'control-test', '--duration', '2.2',
                                   '--step-at', '0.5', '--state-dir', directory])
            stats = await app.run(args, send_override=send, retry_seconds=.001)
            self.assertEqual(attempts[0], attempts[1])
            self.assertEqual(stats['samples'], 22)
            self.assertEqual(stats['sent'], 3)
            self.assertEqual(stats['unsent'], 0)
            rows = [decode_simulation(body, 'control-test') for body in sent]
            self.assertEqual([x['sequence_number'] for x in rows], [1, 2, 3])
            self.assertEqual([x['setpoint_c'] for x in rows], [28, 34, 34])
            self.assertTrue(all(set(x) == set(payload()) for x in rows))
            self.assertTrue(all(0 <= x['heater_voltage'] <= 5 for x in rows))
            await app.run(app.parse_args(['--device-id', 'control-test', '--duration', '.2',
                                        '--state-dir', directory]), send_override=send)
            self.assertEqual(json.loads(sent[-1])['sequence_number'], 4)
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_stalled_network_does_not_stop_control_and_queue_is_bounded(self):
        async def scenario(directory):
            async def send(body):
                await asyncio.sleep(30)
            args = app.parse_args(['--device-id', 'control-test', '--duration', '3.2',
                '--queue-size', '1', '--drain-timeout', '.05', '--state-dir', directory])
            stats = await app.run(args, send_override=send)
            self.assertEqual(stats['samples'], 32)
            self.assertEqual(stats['sent'], 0)
            self.assertEqual(stats['generated'], 4)
            self.assertEqual(stats['dropped'], 2)
            self.assertEqual(stats['unsent'], 2)  # one waiting + one in flight
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_cancellation_stops_control_and_releases_counter(self):
        async def scenario(directory):
            async def send(body):
                await asyncio.sleep(30)
            args = app.parse_args(['--device-id', 'control-test', '--duration', '30',
                                   '--state-dir', directory])
            task = asyncio.create_task(app.run(args, send_override=send))
            await asyncio.sleep(.25)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse((Path(directory)/'control-test.sequence.lock').exists())
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_counter_failure_stops_worker_and_releases_lock(self):
        async def scenario(directory):
            async def send(body):
                self.fail('Must not send without persistent identity')
            args = app.parse_args(['--device-id', 'control-test', '--duration', '30',
                                   '--state-dir', directory])
            with patch.object(app.SequenceCounter, 'next', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    await app.run(args, send_override=send)
            self.assertFalse((Path(directory)/'control-test.sequence.lock').exists())
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


if __name__ == '__main__':
    unittest.main()
