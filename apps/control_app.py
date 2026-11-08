"""Real-time control application - the version meant to run on the Raspberry Pi.

Paced by the wall clock, prints one line per second, and publishes every sample
through an ``on_sample`` callback. That callback is the seam where the rest of
the Industry 4.0 system attaches: MQTT, OPC UA, a REST call or a database
write all fit there without touching the control loop.

No matplotlib, no numpy - this runs on a plain Raspberry Pi Python install.

    python apps/control_app.py                            # simulator, console
    python apps/control_app.py --duration 600             # run for 10 minutes
    python apps/control_app.py --mqtt-host broker.example.com      # publish to MQTT
    python apps/control_app.py --mqtt-host 127.0.0.1 --no-tls      # lab broker

To drive the real rig instead of the simulator, replace the backend:

    from airheater.interfaces.nidaq import NiDaqIO
    io = NiDaqIO(device="Dev1", ai_channel="ai0", ao_channel="ao0")

Nothing else in this file changes. Verify the wiring and the sensor scaling
against your own rig first, and keep the loop inside the ``with`` block so the
heater is driven back to 0 V when the application exits.
"""

from __future__ import annotations

# Make the airheater package importable when this script is run directly,
# from any working directory, without installing it first.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import logging

from airheater import (
    AirHeaterSim,
    LowPassFilter,
    PIController,
    Sample,
    SimulatedIO,
    run_control_loop,
)

Ts = 1.0          # sampling time [s] - 1 s is plenty for a 22 s time constant
Kp = 0.52
Ti = 18.0
Tf = 2.0          # measurement filter time constant [s]
SETPOINT = 28.0   # [degC]
T_INIT = 20.0     # initial outlet temperature [degC]
NOISE_STD = 0.1   # simulated measurement noise [degC]


class ConsolePublisher:
    """Print one line per sample. The default, and a useful fallback."""

    def __call__(self, s: Sample) -> None:
        print(
            "t={0:7.1f}s  r={1:5.2f}  y={2:5.2f}  yf={3:5.2f}  u={4:5.3f}V{5}".format(
                s.t, s.setpoint, s.measured, s.filtered, s.u,
                "  [SAT]" if s.saturated else "",
            )
        )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--duration", type=float, default=300.0,
                   help="run length in seconds (default: 300)")
    p.add_argument("--setpoint", type=float, default=SETPOINT,
                   help="reference temperature in degC (default: %(default)s)")
    p.add_argument("--ts", type=float, default=Ts,
                   help="sampling time in seconds (default: %(default)s)")
    p.add_argument("--mqtt-host", default=None,
                   help="publish samples to this MQTT broker as well")
    p.add_argument("--mqtt-port", type=int, default=None,
                   help="broker port (default 8883 with TLS, 1883 without)")
    p.add_argument("--mqtt-topic", default=None,
                   help="base topic (default airheater/data)")
    p.add_argument("--source", default=None,
                   help="name identifying this rig (default airheater-1)")
    p.add_argument("--no-tls", action="store_true",
                   help="connect without TLS (isolated lab networks only)")
    p.add_argument("--publish-every", type=int, default=None,
                   help="publish one sample in every N (default: one per second)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )

    sim = AirHeaterSim(args.ts, T_init=T_INIT, noise_std=NOISE_STD)
    pi = PIController(Kp=Kp, Ti=Ti, Ts=args.ts,
                      u_min=sim.params.u_min, u_max=sim.params.u_max)
    lp = LowPassFilter(Tf, args.ts)

    console = ConsolePublisher()
    per_second = max(int(round(1.0 / args.ts)), 1)

    publisher = None
    if args.mqtt_host:
        from airheater.telemetry import MqttConfig
        from airheater.telemetry.mqtt import MqttPublisher

        config = MqttConfig.from_env(
            host=args.mqtt_host,
            port=args.mqtt_port,
            topic=args.mqtt_topic,
            source=args.source,
            tls=False if args.no_tls else None,
        )
        every = args.publish_every if args.publish_every else per_second
        publisher = MqttPublisher(config, every=every)
        print("Publishing to {0}:{1} topic {2} (1 in {3} samples)".format(
            config.host, config.port, config.data_topic, every))
        if not publisher.connect(timeout=10.0):
            # Not fatal: paho keeps retrying while the loop runs. Controlling
            # the heater matters more than the telemetry link.
            print("Broker not reachable yet - controlling anyway, will retry.")

    def on_sample(s: Sample) -> None:
        if s.k % per_second == 0:
            console(s)           # one console line per second, whatever Ts is
        if publisher is not None:
            publisher.publish(s)  # decimated by `every`, never raises

    print("Air heater control application - setpoint {0} degC, Ts = {1} s".format(
        args.setpoint, args.ts))
    print("Ctrl-C to stop.\n")

    try:
        # The context manager matters on real hardware: it drives the heater
        # back to 0 V on exit, including after an exception or Ctrl-C.
        with SimulatedIO(sim) as io:
            samples = run_control_loop(
                io, pi, args.setpoint, args.ts,
                duration=args.duration,
                measurement_filter=lp,
                on_sample=on_sample,
                realtime=True,
            )
    finally:
        if publisher is not None:
            # Publishes the retained "offline" status, so the collector knows
            # this was a clean shutdown rather than a failure.
            publisher.close()
            print("\nMQTT: published {0}, dropped {1}".format(
                publisher.published, publisher.dropped))

    if samples:
        jitter = [s.jitter for s in samples]
        print("{0} samples, loop jitter: mean {1:.1f} ms, max {2:.1f} ms".format(
            len(samples), 1000 * sum(jitter) / len(jitter), 1000 * max(jitter)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
