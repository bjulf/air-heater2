"""The whole data path, end to end, without a broker.

Control loop -> MQTT publisher -> broker -> subscriber -> validation -> SQLite,
with the broker replaced by the in-process loopback. Every other component is
the real one, so this shows the actual pipeline working and is a convenient
thing to run in the demonstration video before the real broker is up.

It also feeds the validator three hostile messages, to show that a bad
publisher cannot get rubbish into the database.

    python demos/mqtt_pipeline.py

For the real thing, start a broker and run the two applications instead:

    mosquitto -p 1883 -v
    python apps/database_app.py --host 127.0.0.1 --no-tls
    python apps/control_app.py  --host-flag... see the README
"""

# Make the airheater package importable when this script is run directly,
# from any working directory, without installing it first.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

from airheater import AirHeaterSim, LowPassFilter, PIController, SimulatedIO, run_control_loop
from airheater.storage import SqliteStore
from airheater.telemetry import MqttConfig
from airheater.telemetry.loopback import LoopbackBroker
from airheater.telemetry.mqtt import MqttPublisher, MqttSubscriber

Ts = 0.1
DURATION = 300.0
SETPOINT = 28.0

HOSTILE = [
    (b"not json at all", "not JSON"),
    (b'{"schema":1,"source":"rig-1","k":1,"t":1.0,"setpoint":28.0,'
     b'"measured":1e9,"filtered":28.0,"u":1.0}', "absurd temperature"),
    (b'{"schema":1,"source":"\'; DROP TABLE measurements;--","k":2,"t":1.0,'
     b'"setpoint":28.0,"measured":28.0,"filtered":28.0,"u":1.0}', "SQL in source"),
]


def main() -> int:
    logging.basicConfig(level=logging.ERROR)     # keep the output readable

    broker = LoopbackBroker()
    config = MqttConfig(host="loopback", tls=False, source="rig-1",
                        allow_plaintext_auth=True)

    with SqliteStore(":memory:", commit_every=50) as store:
        subscriber = MqttSubscriber(config, on_measurement=store.store,
                                    client_factory=broker.client_factory)
        subscriber.connect(timeout=0.1)

        publisher = MqttPublisher(config, every=10,   # one message per second
                                  client_factory=broker.client_factory)
        publisher.connect(timeout=0.1)

        print("Running the control loop and publishing to {0} ...".format(
            config.data_topic))
        io = SimulatedIO(AirHeaterSim(Ts, T_init=20.0, noise_std=0.1, seed=1))
        pi = PIController(Kp=0.52, Ti=18.0, Ts=Ts)
        samples = run_control_loop(
            io, pi, SETPOINT, Ts, duration=DURATION,
            measurement_filter=LowPassFilter(2.0, Ts),
            on_sample=publisher.publish, realtime=False,
        )

        print("\nNow feeding the collector three hostile messages:")
        for payload, note in HOSTILE:
            before = subscriber.rejected
            subscriber.handle_message(config.data_topic, payload)
            verdict = "rejected" if subscriber.rejected > before else "ACCEPTED (!)"
            print("  {0:<22} {1}".format(note, verdict))

        store.flush()

        print("\n--- pipeline ---")
        print("  control loop     : {0} samples over {1:.0f} s".format(
            len(samples), DURATION))
        print("  published        : {0} (1 in {1})".format(
            publisher.published, publisher.every))
        print("  accepted         : {0}".format(subscriber.accepted))
        print("  rejected         : {0}".format(subscriber.rejected))
        print("  stored           : {0}".format(store.count()))
        print("  sources          : {0}".format(", ".join(store.sources())))

        print("\n--- last 5 rows in the database ---")
        print("  {0:>5}  {1:>7}  {2:>9}  {3:>9}  {4:>7}".format(
            "k", "t [s]", "setpoint", "filtered", "u [V]"))
        for row in reversed(store.recent(limit=5)):
            print("  {0:>5}  {1:>7.1f}  {2:>9.2f}  {3:>9.2f}  {4:>7.3f}".format(
                row["k"], row["t"], row["setpoint"], row["filtered"], row["u"]))

        final = store.recent(limit=1)[0]
        print("\nFinal temperature {0:.2f} degC against a setpoint of {1:.1f}.".format(
            final["filtered"], SETPOINT))

        publisher.close()
        subscriber.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
