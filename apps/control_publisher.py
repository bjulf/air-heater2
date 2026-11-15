"""Run the analysed PI simulation at 10 Hz with configurable telemetry timing."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater import AirHeaterSim, PIController, LowPassFilter, SimulatedIO, run_control_loop
from airheater.telemetry.simulator import SequenceCounter, send_pending
from airheater.telemetry.azure_transport import create_transport

TS, KP, TI, TF = .1, .52, 18., 2.


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--transport', choices=('stdout', 'azure'), default='stdout')
    p.add_argument('--device-id', default='airheater-sim-1')
    timing = p.add_mutually_exclusive_group()
    timing.add_argument('--duration', type=float, default=120)
    timing.add_argument('--continuous', action='store_true',
                        help='run until Ctrl-C, with bounded memory')
    p.add_argument('--publish-interval', type=float, default=1,
                   help='telemetry interval in seconds; control stays at 0.1 s')
    p.add_argument('--setpoint', type=float, default=28)
    p.add_argument('--target', type=float, default=34)
    p.add_argument('--step-at', type=float, default=20)
    p.add_argument('--disturbance-at', type=float, default=None,
                   help='optional time to lower inlet temperature from 21.5 to 18.5 C')
    p.add_argument('--queue-size', type=int, default=300)
    p.add_argument('--drain-timeout', type=float, default=10)
    p.add_argument('--state-dir', default=str(Path(__file__).resolve().parents[1]/'.state'))
    p.add_argument('--websockets', action='store_true')
    args = p.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.device_id):
        p.error('invalid device-id')
    numbers = [args.duration, args.setpoint, args.target, args.step_at, args.drain_timeout,
               args.publish_interval]
    if args.disturbance_at is not None:
        numbers.append(args.disturbance_at)
    if not all(math.isfinite(v) for v in numbers):
        p.error('numeric arguments must be finite')
    if (args.duration <= 0 or args.step_at < 0 or args.drain_timeout <= 0
            or args.queue_size < 1 or not 20 <= args.setpoint <= 50
            or not 20 <= args.target <= 50
            or (args.disturbance_at is not None and args.disturbance_at < 0)):
        p.error('invalid duration, setpoint, target, disturbance or queue settings')
    if (args.publish_interval < TS or
            not math.isclose(args.publish_interval/TS, round(args.publish_interval/TS), abs_tol=1e-8)):
        p.error('publish-interval must be a positive multiple of 0.1 s')
    if args.continuous:
        args.duration = None
    return args


async def run(args, send_override=None, retry_seconds=2.):
    client = None
    if send_override is None:
        client, send = create_transport(args.transport, args.device_id, args.websockets)
    else:
        send = send_override
    queue = asyncio.Queue(maxsize=args.queue_size)
    loop = asyncio.get_running_loop()
    stopped = threading.Event()
    stats = dict(samples=0, generated=0, dropped=0, sent=0,
                 jitter_max_s=0., jitter_sum_s=0.)
    producer = worker = None

    def enqueue(snapshot):
        stats['generated'] += 1
        try:
            queue.put_nowait(snapshot)
        except asyncio.QueueFull:
            stats['dropped'] += 1
            print('Queue full: dropped newest control measurement', file=sys.stderr)

    def control():
        sim = AirHeaterSim(TS, T_init=args.setpoint, noise_std=.15, seed=20261115)
        u0 = sim.required_u(args.setpoint)
        sim.reset(T_init=args.setpoint, u_init=u0)
        controller = PIController(KP, TI, TS, u_init=u0)
        filt = LowPassFilter(TF, TS, y_init=args.setpoint)

        def reference(t):
            sim.params.T_env = (18.5 if args.disturbance_at is not None
                                and t >= args.disturbance_at else 21.5)
            return args.setpoint if t < args.step_at else args.target

        with SimulatedIO(sim) as io:
            def sample(s):
                stats['samples'] += 1
                stats['jitter_sum_s'] += s.jitter
                stats['jitter_max_s'] = max(stats['jitter_max_s'], s.jitter)
                if s.k % round(args.publish_interval/TS):
                    return
                snapshot = dict(schema='airheater.control.v1', device_id=args.device_id,
                    timestamp=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(s.t, 2),
                    temperature_c=round(s.measured, 2), heater_voltage=round(s.u, 2),
                    setpoint_c=round(s.setpoint, 2))
                loop.call_soon_threadsafe(enqueue, snapshot)

            run_control_loop(io, controller, reference, TS, duration=args.duration,
                measurement_filter=filt, on_sample=sample, realtime=True,
                stop=stopped.is_set, history=False)

    async def delivered(body):
        await send(body)
        stats['sent'] += 1

    try:
        with SequenceCounter(Path(args.state_dir)/(args.device_id+'.sequence')) as counter:
            async def prepare(snapshot):
                # Keep disk I/O out of both control and sender event loop.
                # Finish an in-flight reservation before releasing its file lock.
                reservation = asyncio.create_task(asyncio.to_thread(counter.next))
                try:
                    sequence = await asyncio.shield(reservation)
                except asyncio.CancelledError:
                    await reservation
                    raise
                return json.dumps(dict(snapshot, sequence_number=sequence),
                                  allow_nan=False, separators=(',', ':'))

            worker = asyncio.create_task(send_pending(queue, delivered, retry_seconds,
                                                     prepare=prepare))
            producer = asyncio.create_task(asyncio.to_thread(control))
            try:
                done, _ = await asyncio.wait([worker, producer], return_when=asyncio.FIRST_COMPLETED)
                if worker in done:
                    worker.result()  # propagate counter/serialization failures
                    raise RuntimeError('Telemetry worker stopped unexpectedly')
                await producer
                await asyncio.sleep(0)  # deliver final call_soon_threadsafe callbacks
                drain = asyncio.create_task(queue.join())
                try:
                    done, _ = await asyncio.wait([drain, worker], timeout=args.drain_timeout,
                                                return_when=asyncio.FIRST_COMPLETED)
                    if worker in done:
                        worker.result()
                    if drain not in done:
                        print('Drain timed out; unsent RAM messages will be lost', file=sys.stderr)
                finally:
                    drain.cancel()
                    await asyncio.gather(drain, return_exceptions=True)
            finally:
                stopped.set()
                try:
                    if producer is not None:
                        await asyncio.shield(producer)
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
    finally:
        stopped.set()
        if client is not None:
            try:
                await asyncio.wait_for(client.shutdown(), 10)
            except Exception:
                print('Azure shutdown failed', file=sys.stderr)
        stats['unsent'] = stats['generated']-stats['dropped']-stats['sent']
        stats['jitter_mean_s'] = stats['jitter_sum_s']/max(1, stats['samples'])
        print(json.dumps(stats, sort_keys=True), file=sys.stderr)
    return stats


def main():
    args = parse_args()
    if args.transport == 'azure':
        logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # SDK errors can include credentials; report class only.
        print('Control publisher failed ({})'.format(type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
