# Simulation → IoT Hub → PostgreSQL → Grafana

Current deployed configuration and live verification (2026-11-14) are recorded
in [PRESENTATION_NOTES.md](PRESENTATION_NOTES.md). The working deployment uses
Azure PostgreSQL and an Azure VM receiver; the Compose instructions below are
an alternative local setup. The earlier implementation-only test status below
predates the successful live ingestion and checkpoint-persistence checks.

The publisher continues unchanged, without PI control or SQLite buffering.
Run the receiver on the PC/server with PostgreSQL access; the Pi needs only
its device secret. Use Python 3.12 (the verified Pi environment is `.venv-azure`).
No network configuration changes are needed.

## 1. Private settings and local services

Commands below run from the `code` repository root. Docker Compose v2 and
Docker Engine/Desktop must already be installed and running. Docker was not
available in the implementation environment; service startup remains to test.

Copy `.env.example` to `.env` **only if `.env` does not already exist**.
Keep existing device settings and add the receiver/service settings. Environment
variables override `.env` for Python; interpolation is disabled there. Choose
independent strong passwords for the four Compose variables. In Compose's
`.env` format, use single quotes around values containing `$` to keep them literal.
The DSN's ingest password must match `AIRHEATER_INGEST_PASSWORD`; follow libpq
quoting rules for spaces, quotes and backslashes in DSN values.

```powershell
docker compose up -d
python -m pip install -e ".[receiver]"
```

PostgreSQL is published only on `127.0.0.1:5432`; Grafana only on
`127.0.0.1:3000`. Internal Grafana-to-PostgreSQL traffic uses the local Docker
network without TLS. For a remote database, require verified TLS in the DSN
(`sslmode=verify-full`, configured CA), and adjust Grafana accordingly.
The schema, users and grants are initialized on the **first empty volume only**.
Editing `.env` afterward does not rotate database or Grafana admin passwords.
Do not delete an existing volume to rotate credentials; use administrative
password changes and update private settings instead.

Do not print `.env`, DSNs, `docker compose config`, or connection strings into
logs/chat/screenshots. Compose resolves secrets into its configuration. Protect
`.env` with local user permissions (Pi: `chmod 600 .env`). Never commit it.

## 2. Azure receive access

In IoT Hub `iotlab1`, create a dedicated shared access policy for the receiver
with **Service Connect** only. The publisher's device key cannot read events.
Under **Built-in endpoints**, copy the actual **Event Hub-compatible connection
string**, using that service policy. Set `IOTHUB_EVENTHUB_CONNECTION_STRING` in
the receiver's private `.env`. It has `Endpoint=sb://...;SharedAccessKeyName=...;
SharedAccessKey=...;EntityPath=...` (one line). Copy it from Azure; do not derive
it from the device/service hostname or the IoT Explorer hostname. No separate
Event Hub or Azure Storage account is required.

Create consumer group `airheater-postgres` on the built-in endpoint and set
`IOTHUB_CONSUMER_GROUP` to match. Keep IoT Explorer on a different group. If the
current Azure tier limits available groups, stop Explorer and dedicate an
existing group to this receiver, setting its exact name in `.env`.

```powershell
python apps/azure_receiver.py
```

The receiver uses AMQP/TLS (normally outbound port 5671). Start the Pi publisher
in its existing Python 3.12 environment:

```bash
cd ~/air-heater2
source .venv-azure/bin/activate
python -u apps/simulator_publisher.py --transport azure --duration 30 --step-at 5
```

Use short verification runs: at one event per second the stated Free quota of
8,000 events/day is consumed in roughly 2 hours 13 minutes, before retries and
other device traffic. Check actual quota/retention in the Azure portal.

## 3. Storage and recovery

`simulation_measurements` has primary key `(device_id, sequence_number)`.
Parameterized inserts use `ON CONFLICT DO NOTHING`: the first committed copy
wins. Store three separate `timestamptz` values: `measured_at` from the payload,
`enqueued_at` from IoT Hub, and `received_at` from the database clock. Incoming
timestamps must include a timezone; they are normalized to UTC. Allowed ranges:
sequence 1..signed bigint max, elapsed 0..315360000 seconds, temperature -50..150
°C, input 0..5 V; timestamps from 2020 up to five minutes ahead of receiver time.
Keep host clocks synchronized. Sensor noise explains allowing temperatures
outside the nominal 20..50 °C transmitter range. Identity is checked against
IoT Hub's authenticated `iothub-connection-device-id`, not trusted from JSON alone.

Each callback commits the measurement before updating its partition checkpoint.
Checkpoints are persisted in PostgreSQL's `receiver_checkpoints`, keyed by actual
namespace, hub, group and partition. Database/checkpoint errors retry the same
event every two seconds, without advancing that partition. A crash after insert
but before checkpoint replays an idempotent insert. Retried inserts preserve the
original `received_at`. PostgreSQL restarts preserve both tables in the volume.
Initial startup without a checkpoint replays all events still retained in IoT
Hub (`starting_position="-1"`). An outage longer than Azure retention can still
lose events; neither this receiver nor the publisher's RAM queue is an archive.

Invalid events are deliberately rejected and checkpointed to avoid permanently
blocking a partition. A rejection is logged without payload/secrets. There is no
quarantine/dead-letter store; rejected payloads cannot be recovered here. JSON
is bounded to 4 KiB, exact fields/types are enforced, and NaN/infinity, duplicate
keys, naive/future timestamps and identity mismatches are rejected.

**Run exactly ONE receiver for this consumer group across all machines.** The
checkpoint adapter keeps partition ownership in memory, not distributed leases;
it supports a single lab receiver. On Linux, an OS-managed flock on
`.state/azure_receiver.process.lock` prevents a second process in this checkout
and releases automatically on process death. Never delete that file. A legacy
`.state/azure_receiver.lock` blocks migration until manually inspected after
confirmed process stop. Windows retains the earlier existence lock. See
[systemd installation](deploy/SYSTEMD.md). Never delete/reset publisher `.sequence`
files. Multiple hosts need a distributed checkpoint/lease store before scaling.

## 4. Grafana

Open http://localhost:3000 and log in as `admin` with `GRAFANA_ADMIN_PASSWORD`.
The provisioned **Air heater simulation** dashboard includes temperature,
heater voltage, a **Source** selector, Grafana's time interval picker and age of
the latest measurement. Charts use measurement time; the age panel deliberately
looks across all history so a stale source does not disappear outside the chart
interval. Age can be briefly negative with clock skew. Refresh is five seconds.

Grafana connects as `airheater_grafana`, granted only CONNECT/USAGE/SELECT on the
measurement table. It cannot write measurements or read/write checkpoints.
The receiver has INSERT/SELECT on measurements and INSERT/SELECT/UPDATE on
checkpoints; no schema administration permission. Admin credentials belong only
to database initialization/maintenance. Images track PostgreSQL 16 and Grafana 12;
record tested image digests before a reproducible deployment.

## 5. Verification

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python tests/test_airheater.py
python tests/test_telemetry.py
```

Offline tests exercise invalid payloads, UTC conversion, authenticated identity,
parameter binding, database retry ordering, poison-message skipping and
checkpoint retry. The optional integration test requires `.[receiver]` and
`AIRHEATER_TEST_POSTGRES_DSN` pointing at a **disposable initialized PostgreSQL**
database. It verifies real duplicate insertion and checkpoint roundtrip inside
a rolled-back transaction; otherwise it is explicitly skipped.
The two older suites use module-level test functions, so run their standalone
commands too (unittest discovery does not collect those functions). They require
the existing analysis/MQTT extras respectively.

Live acceptance still required:

Implementation verification (2026-11-14, Windows/Python 3.12): 6 ingestion
unit tests, 2 publisher tests, 42 model/analysis tests and 41 legacy telemetry
tests passed. The real PostgreSQL integration test was skipped (no test DSN).
YAML/JSON files parsed successfully; Azure Event Hubs 5.15.1 EventData decoding
and client construction with the checkpoint adapter were checked locally.
Some pre-existing asyncio/network/temp-file tests required execution outside
the Windows sandbox. No live Azure receive, PostgreSQL transaction, Compose
startup or rendered Grafana dashboard was verified in this environment.

1. Start Compose; confirm database and Grafana are healthy and provisioned.
2. Set receiver secrets and run a 30-second Azure publisher test. Verify chart
   values and all three database times, and confirm durable checkpoints exist.
3. Stop/restart receiver; replay the same `(device_id, sequence_number)` and
   confirm row count stays unchanged. Use a separate test identity/counter;
   never reset the real device counter.
4. During publishing, `docker compose stop postgres`; observe receiver retry.
   `docker compose start postgres`; confirm pending events appear and checkpoints
   resume. Stop/restart the receiver during the outage too, within Azure retention.
5. Send malformed simulation JSON from an authorized test device; confirm a
   rejection and that a subsequent valid event is stored.
6. Connect as Grafana's role and verify INSERT/UPDATE/DELETE are denied.

`docker compose down` stops services while preserving data. Do not use `down -v`
unless intentionally destroying this database. No automatic retention/deletion
policy is installed.

SDK contracts: [Azure Event Hubs / IoT Hub receiving](https://learn.microsoft.com/python/api/overview/azure/eventhub-readme),
[checkpoint store](https://learn.microsoft.com/en-us/python/api/azure-eventhub/azure.eventhub.checkpointstore?view=azure-python),
[psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[Grafana PostgreSQL configuration](https://grafana.com/docs/grafana/latest/datasources/postgres/configure/).
