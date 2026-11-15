"""Receive simulator events from IoT Hub's built-in endpoint into PostgreSQL."""
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.telemetry.ingestion import EventHandler
from airheater.telemetry.receiver_lock import receiver_lock
from airheater.storage.postgres_simulation import PostgresSimulationStore, PostgresCheckpointStore


def receive_error(context, error):
    # Exception messages may include credentials; class name alone is safe.
    print("Azure receive error ({}); SDK will retry".format(type(error).__name__), flush=True)


def main():
    # SDK logs/errors can contain connection details. Emit only our own messages.
    logging.disable(logging.CRITICAL)
    try:
        from dotenv import load_dotenv
        import psycopg
        from azure.eventhub import EventHubConsumerClient
        root = Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env", override=False, interpolate=False)
        connection = os.environ["IOTHUB_EVENTHUB_CONNECTION_STRING"]
        group = os.environ.get("IOTHUB_CONSUMER_GROUP", "airheater-postgres")
        dsn = os.environ["AIRHEATER_POSTGRES_DSN"]
        def connect():
            return psycopg.connect(dsn, connect_timeout=10, options="-c statement_timeout=15000 -c timezone=UTC")
        state = root / ".state"
        with receiver_lock(state):
            store = PostgresSimulationStore(connect)
            if not store.control_schema_ready():
                print('Database migration required: run deploy/control_telemetry_migration.sql as administrator.',
                      file=sys.stderr)
                return 1
            checkpoints = PostgresCheckpointStore(connect)
            client = EventHubConsumerClient.from_connection_string(
                connection, consumer_group=group, checkpoint_store=checkpoints,
                logging_enable=False)
            with client:
                print("Receiving simulation and control events; Ctrl-C to stop", flush=True)
                client.receive(on_event=EventHandler(store),
                               on_error=receive_error,
                               starting_position="-1")
    except KeyboardInterrupt:
        return
    except Exception:
        print("Receiver stopped. Check dependencies, private settings, database and receiver lock.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
