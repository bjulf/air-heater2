"""Remote command validation, control-thread acknowledgement and SDK wiring."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.control.setpoint import RemoteSetpoint, validate_setpoint, install_method_handler


class RemoteSetpointTests(unittest.TestCase):
    def test_strict_range_and_payload(self):
        for payload in ({}, {'setpoint_c': True}, {'setpoint_c': '32'}, {'setpoint_c': 24.9},
                        {'setpoint_c': 36.1}, {'setpoint_c': float('nan')},
                        {'setpoint_c': 32, 'kp': 5}, {'setpoint_c': float('inf')},
                        {'setpoint_c': 10**500}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_setpoint(payload)
        self.assertEqual(validate_setpoint({'setpoint_c': 32.123}), 32.12)

    def test_ack_only_after_control_thread_polls(self):
        state = RemoteSetpoint(28)
        state.start()
        with ThreadPoolExecutor() as executor:
            result = executor.submit(state.submit, {'setpoint_c': 32.12})
            deadline = time.monotonic()+1
            while state.pending is None and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertFalse(result.done())
            self.assertEqual(state.poll(), 32.12)
            self.assertEqual(result.result()[0], 200)
        self.assertEqual(state.poll(), 32.12)
        state.close()

    def test_timeout_never_applies_late_and_stop_rejects(self):
        state = RemoteSetpoint(28)
        self.assertEqual(state.submit({'setpoint_c': 30})[0], 503)
        state.start()
        self.assertEqual(state.submit({'setpoint_c': 30}, timeout=.01)[0], 504)
        self.assertEqual(state.poll(), 28)
        state.close()
        self.assertEqual(state.submit({'setpoint_c': 30})[0], 503)

    def test_conflicting_command_and_shutdown(self):
        state = RemoteSetpoint(28)
        state.start()
        with ThreadPoolExecutor() as executor:
            first = executor.submit(state.submit, {'setpoint_c': 32})
            deadline = time.monotonic()+1
            while state.pending is None and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertEqual(state.submit({'setpoint_c': 33})[0], 409)
            state.close()
            self.assertEqual(first.result()[0], 503)
        self.assertEqual(state.poll(), 28)

    def test_real_publisher_receives_method_and_telemeters_applied_value(self):
        spec = importlib.util.spec_from_file_location('remote_test_app',
            Path(__file__).resolve().parents[1]/'apps'/'control_publisher.py')
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        async def scenario(directory):
            sent = []
            client = SimpleNamespace(send_method_response=AsyncMock(), shutdown=AsyncMock())
            async def send(body):
                sent.append(json.loads(body))
            def install(c, state):
                install_method_handler(c, state, lambda request, code, payload:
                    SimpleNamespace(status=code, payload=payload))
            args = app.parse_args(['--transport', 'azure', '--remote-setpoint',
                '--duration', '1.4', '--publish-interval', '.1', '--step-at', '0',
                '--state-dir', directory, '--device-id', 'remote-test'])
            with patch.object(app, 'create_transport', return_value=(client, send)), \
                    patch.object(app, 'install_method_handler', side_effect=install):
                task = asyncio.create_task(app.run(args))
                await asyncio.sleep(.25)
                await client.on_method_request_received(SimpleNamespace(
                    name='setSetpoint', payload={'setpoint_c': 32.12}))
                self.assertEqual(client.send_method_response.call_args.args[0].status, 200)
                await client.on_method_request_received(SimpleNamespace(
                    name='setSetpoint', payload={'setpoint_c': 100}))
                self.assertEqual(client.send_method_response.call_args.args[0].status, 400)
                await client.on_method_request_received(SimpleNamespace(name='other', payload={}))
                self.assertEqual(client.send_method_response.call_args.args[0].status, 404)
                await task
            self.assertEqual(sent[0]['setpoint_c'], 28)  # automatic step disabled
            self.assertEqual(sent[-1]['setpoint_c'], 32.12)
            self.assertTrue(any(row['heater_voltage'] > 2 for row in sent))
            client.shutdown.assert_awaited_once()
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


if __name__ == '__main__':
    unittest.main()
