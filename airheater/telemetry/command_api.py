"""Setpoint gateway without operator authentication. Bind behind a TLS reverse proxy on the VM.

The service key stays on the VM. No database write access is needed.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from ..control.setpoint import validate_setpoint


def strict_json(raw):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError('duplicate key')
            data[key] = value
        return data
    return json.loads(raw, object_pairs_hook=unique)


class IoTHubCommands:
    def __init__(self, connection, device_id, opener=urlopen):
        fields = dict(part.split('=', 1) for part in connection.split(';') if '=' in part)
        self.host = fields.get('HostName', '').lower()
        self.policy = fields.get('SharedAccessKeyName', '')
        if (not re.fullmatch(r'[a-z0-9-]+\.azure-devices\.net', self.host)
                or not self.policy or 'DeviceId' in fields):
            raise ValueError('Use a service policy connection string, not a device/Event Hubs string')
        self.key = base64.b64decode(fields.get('SharedAccessKey', ''), validate=True)
        if not self.key:
            raise ValueError('Missing service key')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', device_id):
            raise ValueError('Invalid device ID')
        self.device_id, self.opener = device_id, opener

    def invoke(self, value):
        expiry = str(int(time.time())+60)
        resource = quote(self.host, safe='')
        signature = base64.b64encode(hmac.new(self.key,
            (resource+'\n'+expiry).encode(), hashlib.sha256).digest()).decode()
        sas = 'SharedAccessSignature '+urlencode(dict(sr=self.host, sig=signature,
                                                      se=expiry, skn=self.policy))
        payload = dict(methodName='setSetpoint', payload={'setpoint_c': value},
                       connectTimeoutInSeconds=2, responseTimeoutInSeconds=5)
        request = Request('https://'+self.host+'/twins/'+quote(self.device_id, safe='')+
                          '/methods?api-version=2021-04-12',
                          data=json.dumps(payload, allow_nan=False).encode(),
                          headers={'Authorization': sas, 'Content-Type': 'application/json'},
                          method='POST')
        try:
            with self.opener(request, timeout=12) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('Oversized device response')
            result = strict_json(raw)
            code = result.get('status')
            body = result.get('payload')
            if (code == 200 and isinstance(body, dict) and body.get('status') == 'applied'
                    and validate_setpoint({'setpoint_c': body.get('setpoint_c')}) == value):
                return 200, dict(status='applied', setpoint_c=value, device_id=self.device_id)
            if code in (400, 409, 503, 504):
                return code, {'status': 'device_rejected_or_not_running'}
            return 502, {'status': 'invalid_device_confirmation'}
        except HTTPError as exc:
            if exc.code in (404, 408, 504):
                return 503, {'status': 'device_unavailable'}
            return 502, {'status': 'iot_hub_error'}
        except (URLError, TimeoutError, OSError):
            # A timeout is ambiguous: never retry automatically or claim success.
            return 504, {'status': 'confirmation_missing_check_telemetry'}
        except (ValueError, TypeError, AttributeError):
            return 502, {'status': 'invalid_device_confirmation'}


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, commands, grafana_origin):
        origin = urlsplit(grafana_origin)
        if (origin.scheme not in ('http', 'https') or not origin.netloc
                or origin.path not in ('', '/') or origin.query or origin.fragment
                or origin.username or origin.password):
            raise ValueError('Set the exact Grafana origin, without a dashboard path')
        self.commands = commands
        self.grafana_origin = grafana_origin.rstrip('/')
        self.command_lock = threading.Lock()
        self.last_command = -float('inf')
        super().__init__(address, ControlHandler)


class ControlHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # Never log headers, body, keys or attacker-provided URLs.

    def allowed_origin(self):
        origin = self.headers.get('Origin')
        return origin is None or origin == self.server.grafana_origin

    def reply(self, code, body):
        raw = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Vary', 'Origin')
        if self.headers.get('Origin') == self.server.grafana_origin:
            self.send_header('Access-Control-Allow-Origin', self.server.grafana_origin)
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        if not self.allowed_origin():
            return self.reply(403, {'status': 'origin_denied'})
        if self.path != '/api/setpoint':
            return self.reply(404, {'status': 'not_found'})
        self.reply(200, {'status': 'ok'})

    def do_GET(self):
        if self.path in ('/', '/health'):
            return self.reply(200, {'status': 'ready', 'device_id': self.server.commands.device_id})
        self.reply(404, {'status': 'not_found'})

    def do_POST(self):
        if self.path != '/api/setpoint':
            return self.reply(404, {'status': 'not_found'})
        if not self.allowed_origin():
            return self.reply(403, {'status': 'origin_denied'})
        if self.headers.get('Transfer-Encoding'):
            return self.reply(400, {'status': 'unsupported_transfer_encoding'})
        if self.headers.get_content_type() != 'application/json':
            return self.reply(415, {'status': 'json_required'})
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 1 <= length <= 1024:
                return self.reply(413, {'status': 'body_size'})
            value = validate_setpoint(strict_json(self.rfile.read(length)))
        except (ValueError, UnicodeError, RecursionError):
            return self.reply(400, {'status': 'invalid_setpoint', 'minimum_c': 25, 'maximum_c': 36})
        if not self.server.command_lock.acquire(blocking=False):
            return self.reply(409, {'status': 'command_in_progress'})
        try:
            if time.monotonic()-self.server.last_command < 1:
                return self.reply(429, {'status': 'wait_one_second'})
            self.server.last_command = time.monotonic()
            code, body = self.server.commands.invoke(value)
        finally:
            self.server.command_lock.release()
        print(json.dumps(dict(event='setpoint_command', device_id=self.server.commands.device_id,
                              setpoint_c=value, http_status=code)), flush=True)
        self.reply(code, body)
