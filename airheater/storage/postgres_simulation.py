"""PostgreSQL store and durable SDK checkpoints for ONE receiver instance."""
from threading import Lock
import time


class PostgresSimulationStore:
    def __init__(self, connect):
        self.connect = connect

    def control_schema_ready(self):
        """Fail startup before consuming events when the migration is absent."""
        expected = {'setpoint_c'}
        with self.connect() as connection:
            rows = connection.execute("""SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'simulation_measurements'""").fetchall()
        return expected.issubset({row[0] for row in rows})

    def insert(self, data, enqueued):
        # psycopg3 closes and commits on normal context exit, rolls back on error.
        with self.connect() as connection:
            if data['schema'] == 'airheater.control.v1':
                connection.execute("""
                    INSERT INTO simulation_measurements
                    (device_id, sequence_number, measured_at, elapsed_seconds,
                     temperature_c, heater_voltage, enqueued_at, setpoint_c)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (device_id, sequence_number) DO NOTHING
                """, (data['device_id'], data['sequence_number'], data['timestamp'],
                      data['elapsed_seconds'], data['temperature_c'], data['heater_voltage'],
                      enqueued, data['setpoint_c']))
                return
            connection.execute("""
                INSERT INTO simulation_measurements
                (device_id, sequence_number, measured_at, elapsed_seconds,
                 temperature_c, heater_voltage, enqueued_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, sequence_number) DO NOTHING
            """, (data["device_id"], data["sequence_number"], data["timestamp"],
                  data["elapsed_seconds"], data["temperature_c"], data["heater_voltage"], enqueued))


class PostgresCheckpointStore:
    """CheckpointStore protocol; local ownership, durable offsets in PostgreSQL.

    This is intentionally not a distributed lease implementation. Use exactly
    one receiver for the dedicated consumer group, including on other hosts.
    """
    def __init__(self, connect):
        self.connect = connect
        self.ownership = {}
        self.lock = Lock()

    def list_ownership(self, fully_qualified_namespace, eventhub_name, consumer_group, **kwargs):
        with self.lock:
            return [dict(v) for k, v in self.ownership.items()
                    if k[:3] == (fully_qualified_namespace, eventhub_name, consumer_group)]

    def claim_ownership(self, ownership_list, **kwargs):
        claimed = []
        with self.lock:
            for item in ownership_list:
                value = dict(item)
                value["last_modified_time"] = time.time()
                key = tuple(value[k] for k in ("fully_qualified_namespace", "eventhub_name", "consumer_group", "partition_id"))
                self.ownership[key] = value
                claimed.append(dict(value))
        return claimed

    def list_checkpoints(self, fully_qualified_namespace, eventhub_name, consumer_group, **kwargs):
        with self.connect() as connection:
            rows = connection.execute("""SELECT partition_id, event_offset, sequence_number
                FROM receiver_checkpoints WHERE namespace=%s AND hub=%s AND consumer_group=%s""",
                (fully_qualified_namespace, eventhub_name, consumer_group)).fetchall()
        return [dict(fully_qualified_namespace=fully_qualified_namespace,
                     eventhub_name=eventhub_name, consumer_group=consumer_group,
                     partition_id=row[0], offset=row[1], sequence_number=row[2]) for row in rows]

    def update_checkpoint(self, checkpoint, **kwargs):
        values = tuple(checkpoint[k] for k in ("fully_qualified_namespace", "eventhub_name", "consumer_group", "partition_id", "offset", "sequence_number"))
        with self.connect() as connection:
            connection.execute("""INSERT INTO receiver_checkpoints
                (namespace, hub, consumer_group, partition_id, event_offset, sequence_number)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (namespace, hub, consumer_group, partition_id) DO UPDATE
                SET event_offset=EXCLUDED.event_offset, sequence_number=EXCLUDED.sequence_number
                WHERE receiver_checkpoints.sequence_number <= EXCLUDED.sequence_number""", values)
