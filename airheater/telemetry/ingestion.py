"""Simulation trust boundary and commit-before-checkpoint event handler."""
from datetime import datetime, timezone, timedelta
import json
import math
import re
import time


class InvalidTelemetry(ValueError):
    pass


CONTROL_RANGES = {'setpoint_c': (-50, 150)}


def utc_timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise InvalidTelemetry("timestamp type")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidTelemetry("timestamp format") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise InvalidTelemetry("timestamp needs timezone")
    try:
        result = result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise InvalidTelemetry("timestamp range") from None
    if not datetime(2020, 1, 1, tzinfo=timezone.utc) <= result <= datetime.now(timezone.utc) + timedelta(minutes=5):
        raise InvalidTelemetry("timestamp range")
    return result


def decode_simulation(body, authenticated_device):
    if not isinstance(body, (str, bytes)) or len(body) > 4096:
        raise InvalidTelemetry("body size/type")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidTelemetry("duplicate field")
            result[key] = value
        return result
    try:
        data = json.loads(body, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidTelemetry("invalid JSON") from None
    fields = {"schema", "device_id", "sequence_number", "timestamp", "elapsed_seconds", "temperature_c", "heater_voltage"}
    if not isinstance(data, dict):
        raise InvalidTelemetry("schema/fields")
    control = data.get('schema') == 'airheater.control.v1'
    expected = fields | set(CONTROL_RANGES) if control else fields
    if set(data) != expected or data.get('schema') not in ('airheater.simulation.v1', 'airheater.control.v1'):
        raise InvalidTelemetry("schema/fields")
    device = data["device_id"]
    if not isinstance(device, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", device):
        raise InvalidTelemetry("device ID")
    if device != authenticated_device:
        raise InvalidTelemetry("device identity mismatch")
    sequence = data["sequence_number"]
    if type(sequence) is not int or not 1 <= sequence <= 9223372036854775807:
        raise InvalidTelemetry("sequence range/type")
    ranges = dict(elapsed_seconds=(0, 315360000), temperature_c=(-50, 150), heater_voltage=(0, 5))
    if control:
        ranges.update(CONTROL_RANGES)
    for field, (low, high) in ranges.items():
        value = data[field]
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise InvalidTelemetry("numeric range/type")
    data["timestamp"] = utc_timestamp(data["timestamp"])
    return data


class EventHandler:
    def __init__(self, store, retry_seconds=2, sleep=time.sleep):
        self.store, self.retry_seconds, self.sleep = store, retry_seconds, sleep

    def retry(self, action):
        while True:
            try:
                return action()
            except Exception:
                # Never print payloads, connection strings or SDK exception text.
                print("Database/checkpoint unavailable; retrying current event", flush=True)
                self.sleep(self.retry_seconds)

    def __call__(self, context, event):
        if event is None:
            return
        try:
            properties = event.system_properties or {}
            device = properties.get(b"iothub-connection-device-id", properties.get("iothub-connection-device-id"))
            if isinstance(device, bytes):
                device = device.decode("utf-8")
            # Bound allocation before decoding; body_as_str alone has no size bound.
            chunks, size = [], 0
            for chunk in event.body:
                size += len(chunk)
                if size > 4096:
                    raise InvalidTelemetry("body size")
                chunks.append(chunk)
            data = decode_simulation(b"".join(chunks), device)
            enqueued = event.enqueued_time
            if not isinstance(enqueued, datetime) or enqueued.tzinfo is None:
                raise InvalidTelemetry("missing enqueue time")
        except (InvalidTelemetry, UnicodeError, TypeError):
            # Deliberate poison-message policy: count/log rejection, then skip it.
            print("Rejected invalid simulation event", flush=True)
        else:
            self.retry(lambda: self.store.insert(data, enqueued.astimezone(timezone.utc)))
        # Synchronous callback never returns on a DB failure, so later events on
        # this partition cannot advance past the failed event.
        self.retry(lambda: context.update_checkpoint(event))
