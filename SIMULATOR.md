# Standalone simulation publisher

No controller, database or Azure account is needed for a local smoke test:

```powershell
python code/apps/simulator_publisher.py --duration 30 --step-at 5
```

The default output is JSON on stdout. Temperature starts at ambient; the
heater input steps from 0 to 2 V after 10 seconds. Sampling is once per second.
The measurement is taken at the start of the interval; heater_voltage is the
input applied over the following interval. No setpoint is invented.

## Azure IoT Hub

Create/register an IoT Hub device named `airheater-sim-1`. From the repository
root (the directory containing `pyproject.toml`), install the Azure extra:

```powershell
python -m pip install -e ".[azure]"
Copy-Item .env.example .env
notepad .env
```

On the Pi, create a private file before inserting the secret:

```bash
cd ~/air-heater2
source .venv/bin/activate
python -m pip install -e '.[azure]'
(umask 077; cp -n .env.example .env)
chmod 600 .env
nano .env
```

Set `IOTHUB_DEVICE_CONNECTION_STRING` to the device connection string on one
line. In nano, save with Ctrl+O, Enter, then exit with Ctrl+X. Start sending:

```bash
python apps/simulator_publisher.py --transport azure --duration 30 --step-at 5
```

The publisher loads `.env` beside `pyproject.toml`, regardless of the working
directory. Existing environment variables take priority; use
`unset IOTHUB_DEVICE_CONNECTION_STRING` on the Pi if an old exported value
should be replaced by the file. Console-only simulation does not need dotenv.
The file is plaintext, not an encrypted secret store. `.env` and `.env.*` are
ignored by Git; `.env.example` is the only committed template. Never commit
the actual connection string. Use `--websockets` if port 8883 is
blocked (uses port 443). Observe device-to-cloud events in Azure IoT Explorer
to confirm delivery. PostgreSQL ingestion and Grafana are the next stage;
the existing control-loop collector does not accept this simulation schema.

## Buffering and identity

The RAM queue holds at most 300 waiting measurements, plus one in-flight
message. A background task retries the same JSON and message ID every two
seconds after a failed send. A full queue drops the newest measurement and
reports it; simulation continues. SDK operations may have their own timeout.
Normal completion waits up to 10 seconds to drain. Ctrl-C or process failure
loses all unsent messages. IoT Hub acknowledgement is not database confirmation.

`device_id` plus `sequence_number` identifies a measurement. A tiny counter
file in `code/.state/` is updated before enqueueing. Gaps are expected after
overflow or crashes. Preserve this directory for a given device ID, including
between stdout tests and Azure runs. Do not share a device ID between machines
with independent counters. Never reset a counter for an existing identity.

Only one process can use a counter file at a time. After a hard crash, a stale
`.sequence.lock` file may remain: remove only that lock after verifying the
publisher is stopped. Malformed counters fail rather than silently resetting.
This is a lab counter file, not a guarantee against disk loss or restored backups.

Options: `--device-id`, `--interval`, `--voltage`, `--step-at`, `--queue-size`,
`--drain-timeout`, `--state-dir`. Numeric values must be finite; voltage is 0-5 V.

Tests:

```powershell
python code/tests/test_simulator_publisher.py
```
