"""Publish the open-loop air heater simulation to stdout or Azure IoT Hub."""
import argparse
import asyncio
from datetime import datetime, timezone
import math
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.simulation.model import AirHeaterSim
from airheater.telemetry.simulator import SequenceCounter, encode_telemetry, send_pending


async def run(args):
    client = None
    if args.transport == "azure":
        from dotenv import load_dotenv
        # Resolve relative to the repository, not the shell's working directory.
        # Keep externally supplied values and preserve literal credential bytes.
        load_dotenv(Path(__file__).resolve().parents[1] / ".env",
                    override=False, interpolate=False)
        connection = os.environ.get("IOTHUB_DEVICE_CONNECTION_STRING", "").strip()
        if not connection:
            raise ValueError("Set IOTHUB_DEVICE_CONNECTION_STRING in .env or the environment")
        from azure.iot.device import Message
        from azure.iot.device.aio import IoTHubDeviceClient
        fields = dict(part.split("=", 1) for part in connection.split(";") if "=" in part)
        if fields.get("DeviceId") != args.device_id:
            raise ValueError("DeviceId in connection string must match --device-id")
        client = IoTHubDeviceClient.create_from_connection_string(connection, websockets=args.websockets)

        async def send(body):
            message = Message(body)
            message.content_type = "application/json"
            message.content_encoding = "utf-8"
            message.message_id = "{}:{}".format(args.device_id, json.loads(body)["sequence_number"])
            await client.send_message(message)
    else:
        async def send(body):
            print(body)

    queue = asyncio.Queue(maxsize=args.queue_size)
    worker = None
    generated = dropped = 0
    sim = AirHeaterSim(args.interval, method="zoh", noise_std=0.1, seed=42)
    state = Path(args.state_dir) / (args.device_id + ".sequence")
    try:
        with SequenceCounter(state) as counter:
            worker = asyncio.create_task(send_pending(queue, send))
            start = asyncio.get_running_loop().time()
            while generated * args.interval < args.duration:
                elapsed = generated * args.interval
                voltage = 0.0 if elapsed < args.step_at else args.voltage
                body = encode_telemetry(args.device_id, counter.next(),
                    datetime.now(timezone.utc).isoformat(), elapsed, sim.measure(), voltage)
                try:
                    queue.put_nowait(body)
                except asyncio.QueueFull:
                    dropped += 1
                    print("Queue full: dropped newest measurement", file=sys.stderr)
                sim.step(voltage)
                generated += 1
                await asyncio.sleep(max(0, start + generated * args.interval - asyncio.get_running_loop().time()))
            try:
                await asyncio.wait_for(queue.join(), timeout=args.drain_timeout)
            except asyncio.TimeoutError:
                print("Drain timed out; remaining messages will be lost", file=sys.stderr)
    finally:
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        if client is not None:
            try:
                await asyncio.wait_for(client.shutdown(), timeout=10)
            except Exception:
                print("Azure client shutdown failed", file=sys.stderr)
        print("Generated {}, dropped on overflow {}".format(generated, dropped), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdout", "azure"), default="stdout")
    parser.add_argument("--device-id", default="airheater-sim-1")
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--step-at", type=float, default=10)
    parser.add_argument("--voltage", type=float, default=2)
    parser.add_argument("--queue-size", type=int, default=300)
    parser.add_argument("--drain-timeout", type=float, default=10)
    parser.add_argument("--state-dir", default=str(Path(__file__).resolve().parents[1] / ".state"))
    parser.add_argument("--websockets", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.device_id):
        parser.error("device-id must contain 1-64 letters, digits, underscores or hyphens")
    if any(not math.isfinite(v) for v in (args.duration, args.interval, args.step_at, args.voltage, args.drain_timeout)):
        parser.error("numeric arguments must be finite")
    if args.interval <= 0 or args.duration <= 0 or args.step_at < 0 or args.drain_timeout <= 0 or args.queue_size < 1 or not 0 <= args.voltage <= 5:
        parser.error("invalid timing, queue size or voltage (expected 0-5 V)")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
