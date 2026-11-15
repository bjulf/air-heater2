"""Thread-safe remote setpoint, acknowledged only by the control thread."""
import asyncio
import math
import threading
import time

MIN_SETPOINT, MAX_SETPOINT = 25., 36.


def validate_setpoint(payload):
    if not isinstance(payload, dict) or set(payload) != {'setpoint_c'}:
        raise ValueError('Expected only setpoint_c')
    value = payload['setpoint_c']
    if (type(value) not in (int, float) or not MIN_SETPOINT <= value <= MAX_SETPOINT
            or not math.isfinite(value)):
        raise ValueError('Setpoint must be a finite number from 25 to 36 C')
    return round(float(value), 2)


class RemoteSetpoint:
    def __init__(self, initial):
        self.active = validate_setpoint({'setpoint_c': initial})
        self.lock = threading.Lock()
        self.pending = None
        self.ready = False

    def start(self):
        with self.lock:
            self.ready = True

    def poll(self):
        """Called once per control iteration; no network or disk operations."""
        with self.lock:
            cmd = self.pending
            if cmd is not None:
                if time.monotonic() < cmd['deadline']:
                    self.active = cmd['value']
                    cmd['result'] = (200, {'status': 'applied', 'setpoint_c': self.active})
                else:
                    cmd['result'] = (504, {'status': 'control_timeout'})
                self.pending = None
                cmd['done'].set()
            return self.active

    def submit(self, payload, timeout=2.):
        try:
            value = validate_setpoint(payload)
        except ValueError:
            return 400, {'status': 'invalid_setpoint', 'minimum_c': 25, 'maximum_c': 36}
        cmd = dict(value=value, done=threading.Event(),
                   deadline=time.monotonic()+timeout, result=None)
        with self.lock:
            if not self.ready:
                return 503, {'status': 'control_not_running'}
            if self.pending is not None:
                return 409, {'status': 'another_command_pending'}
            self.pending = cmd
        cmd['done'].wait(timeout)
        with self.lock:
            if cmd['result'] is not None:
                return cmd['result']
            if self.pending is cmd:
                self.pending = None  # timed-out commands must never apply later
            return 504, {'status': 'control_timeout'}

    def close(self):
        with self.lock:
            self.ready = False
            if self.pending is not None:
                cmd, self.pending = self.pending, None
                cmd['result'] = (503, {'status': 'control_stopped'})
                cmd['done'].set()


def install_method_handler(client, state, response_factory=None):
    if response_factory is None:
        from azure.iot.device import MethodResponse
        response_factory = MethodResponse.create_from_method_request

    async def handle(request):
        if request.name != 'setSetpoint':
            code, payload = 404, {'status': 'unknown_method'}
        else:
            code, payload = await asyncio.to_thread(state.submit, request.payload)
        await client.send_method_response(response_factory(request, code, payload))

    client.on_method_request_received = handle
