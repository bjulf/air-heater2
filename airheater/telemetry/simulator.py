"""Open-loop telemetry with a bounded RAM queue and persistent sequence counter."""
import asyncio
import json
import os
from pathlib import Path


class SequenceCounter:
    """One counter file per device; only one publisher may use it at a time.

    Reserve before enqueueing: crashes may leave gaps but never reuse a number.
    A corrupt file fails closed. Do not delete/reset it for an existing device.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(self.path.suffix + ".lock")
        self.fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            self.value = int(self.path.read_text()) if self.path.exists() else 0
            if self.value < 0:
                raise ValueError("negative sequence counter")
        except BaseException:
            self.close()
            raise

    def next(self):
        value = self.value + 1
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w") as stream:
            stream.write(str(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(self.path))
        self.value = value
        return value

    def close(self):
        os.close(self.fd)
        self.lock.unlink()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


async def send_pending(queue, send, retry_seconds=2.0):
    """Retry the identical serialized message; acknowledge only successful sends."""
    while True:
        body = await queue.get()
        while True:
            try:
                await send(body)
                break
            except Exception as exc:
                # Do not log exception text: transport errors can contain credentials.
                print("Send failed ({}); retrying".format(type(exc).__name__))
                await asyncio.sleep(retry_seconds)
        queue.task_done()


def encode_telemetry(device_id, sequence, timestamp, elapsed, temperature, voltage):
    """Separate simulation schema; does not invent a setpoint or control error."""
    return json.dumps({
        "schema": "airheater.simulation.v1",
        "device_id": device_id,
        "sequence_number": sequence,
        "timestamp": timestamp,
        "elapsed_seconds": elapsed,
        "temperature_c": temperature,
        "heater_voltage": voltage,
    }, allow_nan=False, separators=(",", ":"))
