"""Receive simulator events from IoT Hub's built-in endpoint into PostgreSQL."""
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.telemetry.ingestion import EventHandler
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
        state.mkdir(exist_ok=True)
        lock = state / "azure_receiver.lock"
        # Single local process guard. Never touches publisher counter files.
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            checkpoints = PostgresCheckpointStore(connect)
            client = EventHubConsumerClient.from_connection_string(
                connection, consumer_group=group, checkpoint_store=checkpoints,
                logging_enable=False)
            with client:
                print("Receiving simulation events; Ctrl-C to stop", flush=True)
                client.receive(on_event=EventHandler(PostgresSimulationStore(connect)),
                               on_error=receive_error,
                               starting_position="-1")
        finally:
            os.close(fd)
            lock.unlink()
    except KeyboardInterrupt:
        return
    except Exception:
        print("Receiver stopped. Check dependencies, private settings, database and receiver lock.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
