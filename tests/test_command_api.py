"""Local HTTP API tests; outbound Azure communication is replaced by a fake."""
import base64
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, MagicMock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.telemetry.command_api import ControlServer, IoTHubCommands

ORIGIN = 'https://example.grafana.azure.com'


class ControlApiTests(unittest.TestCase):
    def setUp(self):
        self.commands = Mock(device_id='test-device')
        self.commands.invoke.side_effect = lambda value: (200, dict(
            status='applied', setpoint_c=value, device_id='test-device'))
        self.server = ControlServer(('127.0.0.1', 0), self.commands, ORIGIN)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01))
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, data='{"setpoint_c":32.123}', token=None, origin=ORIGIN, method='POST', path='/api/setpoint'):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json', 'Origin': origin}
        if token is not None:
            headers['Authorization'] = 'Bearer '+token
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read()), dict(response.getheaders())
        connection.close()
        return result

    def test_command_without_token_and_cors(self):
        code, body, headers = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body['setpoint_c'], 32.12)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        self.commands.invoke.assert_called_once_with(32.12)

    def test_datasource_base_url_health_check(self):
        for path in ('/', '/health'):
            code, body, _ = self.request(method='GET', path=path)
            self.assertEqual(code, 200)
            self.assertEqual(body, {'status': 'ready', 'device_id': 'test-device'})
        self.assertEqual(self.request(method='GET', path='/missing')[0], 404)
        self.commands.invoke.assert_not_called()

    def test_origin_and_validation_do_not_invoke(self):
        for token, origin, data, expected in [
                (None, 'https://other.example', '{}', 403),
                (None, ORIGIN, '{"setpoint_c":37}', 400),
                (None, ORIGIN, '{"setpoint_c":true}', 400),
                (None, ORIGIN, '{"setpoint_c":NaN}', 400),
                (None, ORIGIN, '{"setpoint_c":31,"setpoint_c":32}', 400),
                (None, ORIGIN, '{"setpoint_c":31,"kp":4}', 400),
                (None, ORIGIN, 'x'*1025, 413)]:
            with self.subTest(expected=expected, data=data[:50]):
                self.assertEqual(self.request(data, token, origin)[0], expected)
        self.commands.invoke.assert_not_called()

    def test_preflight_without_token_and_rate_limit(self):
        self.assertEqual(self.request(method='OPTIONS', token=None)[0], 200)
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request()[0], 429)
        self.assertEqual(self.commands.invoke.call_count, 1)

    def test_device_failure_is_not_reported_as_success(self):
        self.commands.invoke.side_effect = lambda _: (503, {'status': 'device_unavailable'})
        self.assertEqual(self.request()[0], 503)


class IoTHubCommandTests(unittest.TestCase):
    def client(self, body):
        opener = MagicMock()
        opener.return_value.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
        connection = ('HostName=testhub.azure-devices.net;SharedAccessKeyName=setpoint-control;'
                      'SharedAccessKey='+base64.b64encode(b'test-key-only').decode())
        return IoTHubCommands(connection, 'test-device', opener), opener

    def test_exact_rest_request_and_confirmation(self):
        client, opener = self.client(dict(status=200, payload=dict(status='applied', setpoint_c=32)))
        self.assertEqual(client.invoke(32)[0], 200)
        request = opener.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertTrue(request.full_url.endswith('/twins/test-device/methods?api-version=2021-04-12'))
        self.assertEqual(json.loads(request.data)['payload'], {'setpoint_c': 32})
        sas = parse_qs(request.get_header('Authorization').split(' ', 1)[1])
        self.assertEqual(sas['sr'], ['testhub.azure-devices.net'])
        self.assertEqual(sas['skn'], ['setpoint-control'])
        self.assertNotIn('test-key-only', request.get_header('Authorization'))

    def test_reject_false_or_mismatched_confirmation(self):
        for payload in ({'status': 'queued', 'setpoint_c': 32},
                        {'status': 'applied', 'setpoint_c': 30},
                        {'status': 'applied', 'setpoint_c': 200}):
            client, _ = self.client(dict(status=200, payload=payload))
            self.assertEqual(client.invoke(32)[0], 502)

    def test_no_retry_on_unknown_delivery_or_offline_device(self):
        client, opener = self.client({})
        opener.side_effect = URLError('details must not be logged')
        self.assertEqual(client.invoke(32)[0], 504)
        self.assertEqual(opener.call_count, 1)
        opener.side_effect = HTTPError('https://test', 404, 'offline', {}, None)
        self.assertEqual(client.invoke(32)[0], 503)


if __name__ == '__main__':
    unittest.main()
