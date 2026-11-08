"""Database application: subscribe to MQTT, validate, store.

The middle tier of the Industry 4.0 system. The control applications never
touch the database - they publish to a broker, and this one collector
subscribes and writes. That is the arrangement the task asks for, and the
reasons are worth stating in the report:

  * the Pi needs no database credentials, so a stolen Pi yields none;
  * the database is not exposed to the process network at all;
  * several rigs share one collector, and a rig can come and go without the
    database noticing;
  * the broker buffers while the database is down, instead of the control
    application blocking on a write.

Every message is validated by
:func:`~airheater.telemetry.payload.decode_measurement` before it reaches the
store, and every statement binds its parameters. Nothing from the network ever
becomes part of a SQL statement.

    python apps/database_app.py --host broker.example.com
    python apps/database_app.py --host 127.0.0.1 --no-tls --db airheater.db
    python apps/database_app.py --stats            # read the database, no broker

Credentials come from the environment, never the command line - an argument is
visible to every other process on the machine in the process list:

    set AIRHEATER_MQTT_USERNAME=collector
    set AIRHEATER_MQTT_PASSWORD=...
"""

from __future__ import annotations

# Make the airheater package importable when this script is run directly,
# from any working directory, without installing it first.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import logging
import signal
import threading
import time

from airheater.storage import SqliteStore
from airheater.telemetry import MqttConfig, Measurement
from airheater.telemetry.mqtt import MqttSubscriber

log = logging.getLogger("database_app")


class Collector:
    """Glue between the subscriber and the store, plus some counters."""

    def __init__(self, store: SqliteStore) -> None:
        self.store = store
        self.stored = 0
        self.duplicates = 0
        self.started = time.time()
        self.sources = {}

    def on_measurement(self, m: Measurement) -> None:
        if self.store.store(m):
            self.stored += 1
        else:
            self.duplicates += 1
        self.sources.setdefault(m.source, {"last_k": None, "status": "online"})
        self.sources[m.source]["last_k"] = m.k

    def on_status(self, source: str, status: str) -> None:
        entry = self.sources.setdefault(source, {"last_k": None, "status": status})
        entry["status"] = status
        log.info("source %s is now %s", source, status)

    def summary(self, rejected: int) -> str:
        uptime = time.time() - self.started
        parts = [
            "up {0:.0f} s".format(uptime),
            "stored {0}".format(self.stored),
            "duplicates {0}".format(self.duplicates),
            "rejected {0}".format(rejected),
        ]
        for name, info in sorted(self.sources.items()):
            parts.append("{0}: {1} (k={2})".format(name, info["status"], info["last_k"]))
        return " | ".join(parts)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default=None, help="MQTT broker host")
    p.add_argument("--port", type=int, default=None, help="MQTT broker port")
    p.add_argument("--topic", default=None, help="base topic (default airheater/data)")
    p.add_argument("--db", default="airheater.db", help="SQLite database file")
    p.add_argument("--no-tls", action="store_true",
                   help="connect without TLS (isolated lab networks only)")
    p.add_argument("--duration", type=float, default=None,
                   help="stop after this many seconds (default: run until Ctrl-C)")
    p.add_argument("--stats", action="store_true",
                   help="print what is already in the database and exit")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


def print_stats(db_path: str) -> int:
    with SqliteStore(db_path) as store:
        total = store.count()
        print("database : {0}".format(db_path))
        print("rows     : {0}".format(total))
        sources = store.sources()
        if not sources:
            print("no data yet")
            return 0
        print("sources  : {0}".format(", ".join(sources)))
        for source in sources:
            rows = store.recent(source, limit=1)
            if not rows:
                continue
            r = rows[0]
            print(
                "\n  {0}: {1} rows, latest k={2} t={3:.1f}s "
                "setpoint={4:.2f} filtered={5:.2f} u={6:.3f}V".format(
                    source, store.count(source), r["k"], r["t"],
                    r["setpoint"], r["filtered"], r["u"],
                )
            )
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.stats:
        return print_stats(args.db)

    config = MqttConfig.from_env(
        host=args.host,
        port=args.port,
        topic=args.topic,
        tls=False if args.no_tls else None,
    )

    stop = threading.Event()

    def handle_signal(signum, frame):
        log.info("shutting down")
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)

    with SqliteStore(args.db) as store:
        collector = Collector(store)
        subscriber = MqttSubscriber(
            config,
            on_measurement=collector.on_measurement,
            on_status=collector.on_status,
        )
        log.info("collecting from %s:%s %s into %s",
                 config.host, config.port, config.subscribe_pattern(), args.db)

        if not subscriber.connect(timeout=10.0):
            log.warning("broker not reachable yet; paho will keep retrying")

        try:
            deadline = None if args.duration is None else time.time() + args.duration
            while not stop.is_set():
                if deadline is not None and time.time() >= deadline:
                    break
                # Commit whatever is pending, so --stats in another window
                # shows current data rather than the last full batch.
                store.flush()
                log.info("%s", collector.summary(subscriber.rejected))
                stop.wait(10.0)
        finally:
            subscriber.close()
            store.flush()
            log.info("final: %s", collector.summary(subscriber.rejected))

    return 0


if __name__ == "__main__":
    sys.exit(main())
