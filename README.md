# Air heater simulator, control application and stability analysis

For the new 10 Hz PI simulation with 1 Hz Azure telemetry, additive database
migration and Pi/VM rollout commands, see [CONTROL_TELEMETRY.md](CONTROL_TELEMETRY.md).
This version is prepared locally; live deployment remains to be performed.

For the completed simulation-only control theory study, including the Norwegian
report and sampled stability analysis with the measurement filter, see
[CONTROL_THEORY.md](CONTROL_THEORY.md). Reproduce it with
`python demos/control_study.py`. Physical testing is excluded following the
teacher clarification reported by the student.

For the selected simulation → Azure IoT Hub → PostgreSQL → Grafana path,
see [INGESTION.md](INGESTION.md). The MQTT/control collector below is a separate legacy path.

For standalone simulator publishing to stdout or Azure IoT Hub, see
[SIMULATOR.md](SIMULATOR.md). It uses a bounded in-memory queue and a persistent
sequence counter, without running the controller.

Python implementation of the air heater for LAB 3 (Industry 4.0 and Cyber
Security), Part 1: a discrete simulator of the process, a discrete PI
controller, a measurement filter, a real-time control loop that runs on a
Raspberry Pi, and the frequency response and stability analysis.

## Layout

```
code/
├── airheater/              the package
│   ├── simulation/         process model
│   │   ├── model.py        AirHeaterSim, AirHeaterParams   (stdlib only)
│   │   └── batch.py        vectorised simulation, sweeps   (numpy)
│   ├── control/            the control application
│   │   ├── controller.py   PIController                    (stdlib only)
│   │   ├── filters.py      LowPassFilter                   (stdlib only)
│   │   └── loop.py         run_control_loop, Sample        (stdlib only)
│   ├── interfaces/         the boundary to the plant
│   │   ├── base.py         AnalogIO
│   │   ├── simulated.py    SimulatedIO
│   │   └── nidaq.py        NiDaqIO  (NI USB-6008, untested against hardware)
│   ├── analysis/           offline analysis                (numpy)
│   │   ├── frequency_response.py   margins, Bode data
│   │   ├── tuning.py               Ziegler-Nichols, Skogestad SIMC
│   │   └── pade.py                 Padé, closed loop poles
│   ├── telemetry/          publishing and receiving process data
│   │   ├── payload.py      wire format and validation   (the trust boundary)
│   │   ├── config.py       broker settings, credentials from the environment
│   │   ├── mqtt.py         MqttPublisher, MqttSubscriber    (paho-mqtt)
│   │   └── loopback.py     in-process broker, for tests and demos
│   └── storage/            where measurements end up
│       ├── base.py         MeasurementStore, and the SQL injection rule
│       ├── sqlite_store.py SqliteStore          (stdlib, the default)
│       └── sqlserver_store.py  SqlServerStore   (pyodbc, untested)
├── apps/
│   ├── control_app.py      the Raspberry Pi application: real-time loop + MQTT
│   └── database_app.py     the collector: MQTT -> validate -> database
├── demos/
│   ├── step_response.py    open loop, with the model parameters marked
│   ├── closed_loop.py      closed loop PI with noise and filtering
│   ├── stability.py        Bode, margins, pole map, tuning comparison
│   ├── tuning_sweep.py     Kp sweep and a Kp/Ti IAE grid
│   └── mqtt_pipeline.py    the whole data path end to end, without a broker
├── tests/
│   ├── test_airheater.py   42 checks: model, controller, analysis
│   └── test_telemetry.py   41 checks: payload, storage, MQTT, SQL injection
├── conftest.py             makes the package importable under pytest
└── pyproject.toml
```

The split is by what each part needs. The **control application**
(`simulation/model.py`, `control/`, `interfaces/`) is standard library only: a
loop that handles one sample at a time has nothing to gain from numpy, and
this way the Pi needs no scientific stack to run the process. The **offline
work** (`simulation/batch.py`, `analysis/`) uses numpy, where it earns its
place, and is imported on demand so it never loads on the Pi.

## Quick start

Run anything directly — the scripts put the package on `sys.path` themselves,
from any working directory:

```bash
python tests/test_airheater.py && python tests/test_telemetry.py
```

```bash
python demos/mqtt_pipeline.py
```

```bash
python demos/step_response.py
```

```bash
python demos/stability.py
```

```bash
python demos/tuning_sweep.py
```

```bash
python apps/control_app.py --duration 120
```

Or install it properly, which also lets `pytest` and `import airheater` work
from anywhere:

```bash
pip install -e "code[analysis]"
```

Dependencies: numpy and matplotlib for the demos and the analysis; nothing for
the control application itself. `nidaqmx` for the real rig, `paho-mqtt` for
publishing — both optional extras in `pyproject.toml`.

## The model

Continuous, 1. order with transport delay:

```
dTout/dt = (1/theta_t) * (-Tout + K_h * u(t - theta_d) + T_env)
```

Two discretisations, chosen with `method=`:

- **`"euler"`** (default) — Euler forward, the one the course asks you to
  derive and the one to show in the report:
  `Tout[k+1] = Tout[k] + (Ts/theta_t)*(-Tout[k] + K_h*u[k-Nd] + T_env)`
- **`"zoh"`** — exact over one sampling interval, since `u` is held constant
  across it: `Tout[k+1] = a*Tout[k] + (1-a)*(K_h*u[k-Nd] + T_env)`, `a = exp(-Ts/theta_t)`

Euler is accurate at `Ts = 0.1 s` and is rejected above `theta_t/5`. ZOH is
exact at any `Ts`, which matters if you sample slowly on the Pi —
`demos/step_response.py` shows the difference (0.13 °C error at `Ts = 4 s`).

The delay is a FIFO of past control signals, so there are no negative-index
tricks and it is correct from the first sample.

Nominal parameters — identify your own rig from its step response before tuning
against it:

| Parameter | Value | Meaning |
|---|---|---|
| `K_h` | 3.5 °C/V | heater gain |
| `theta_t` | 22 s | time constant |
| `theta_d` | 2 s | transport delay |
| `T_env` | 21.5 °C | ambient/inlet temperature |
| `u` | 0–5 V | heater control signal |
| sensor | 1–5 V ↔ 20–50 °C | outlet temperature transmitter |

With `u` capped at 5 V the process cannot exceed `3.5 × 5 + 21.5 = 39 °C`. A
setpoint above that saturates the heater permanently.

## Integrating it into your Raspberry Pi application

Everything the control application touches goes through `AnalogIO`, so the
simulator and the real rig are interchangeable:

```python
from airheater import AirHeaterSim, LowPassFilter, PIController, SimulatedIO, run_control_loop

Ts = 1.0
io = SimulatedIO(AirHeaterSim(Ts, noise_std=0.1))     # <- the only line that changes
pi = PIController(Kp=0.52, Ti=18.0, Ts=Ts)
lp = LowPassFilter(Tf=2.0, Ts=Ts)

def publish(sample):
    client.publish("airheater/data", json.dumps(sample.as_dict()))

with io:
    run_control_loop(io, pi, setpoint=28.0, Ts=Ts,
                     measurement_filter=lp, on_sample=publish, realtime=True)
```

When the rig is wired up, swap one line:

```python
from airheater.interfaces.nidaq import NiDaqIO
io = NiDaqIO(device="Dev1", ai_channel="ai0", ao_channel="ao0")
```

`NiDaqIO` lives in its own module and is not re-exported, so importing the
package never pulls in the NI driver. It is written against the `nidaqmx` API
but **has not been tested against hardware** — check channel names, wiring and
scaling against your own rig first. Keep the loop inside the `with` block:
closing the backend drives the heater back to 0 V, including after Ctrl-C or
an exception.

`on_sample` is the seam for the rest of the Industry 4.0 system — MQTT, OPC UA,
REST, or a database write. It is called once per sample with a `Sample`, and
`Sample.as_dict()` is a flat, JSON-ready dict. Exceptions inside the callback
are reported but never stop the loop: losing the network must not stop process
control.

## Controller

Velocity (incremental) form, which gives anti-windup for free — the saturated
output is fed back as `u[k-1]`, so the controller cannot accumulate integral
action while the heater sits at its limit:

```
u[k] = u[k-1] + Kp*(e[k] - e[k-1]) + Kp*(Ts/Ti)*e[k]
```

**One deviation from the course slides, worth a line in your report.** The
slides give the integral term as `(Kp/Ti)*e[k]`, without the sampling time.
That drops a factor `Ts` and makes integral action `1/Ts` times too strong —
10× at `Ts = 0.1 s`. It is why the slide example backs off to `Kp = 0.1`
instead of using the `Kp = 0.52` its own stability analysis produced. This
implementation uses the correct form; pass `slide_form=True` to reproduce the
slide behaviour side by side.

## Stability analysis

```python
from airheater.analysis import margins, ziegler_nichols, skogestad_simc
print(margins(Kp=0.52, Ti=18.0))
```

```
wc    = 0.0870 rad/s
w180  = 0.7789 rad/s
GM    = 9.41 (19.47 dB)
PM    = 75.1 deg
Kc    = 4.89
asymptotically stable (wc < w180)
```

These match the values in the course material (GM 9.41, Kc 4.89), which is a
useful cross-check, but they are obtained differently. **The course replaces
the delay with a Padé approximation before computing margins. That is only
needed for a rational transfer function — a frequency response does not need
one.** On the jω axis the delay is exactly `exp(-jω·theta_d)`: magnitude 1,
phase `-ω·theta_d`. So this module uses the exact delay and the margins are not
limited by an approximation order. Padé is kept in its own module and used only
for the pole map, where a rational model is unavoidable.

One implementation detail worth knowing if you adapt the code: `numpy.angle`
wraps into (−π, π], which hides the −180° crossing the analysis is looking for.
`loop_phase()` assembles the phase from its parts instead, each naturally
unwrapped.

### Tuning rules

`demos/stability.py` prints this table:

| rule | Kp | Ti [s] | GM | PM [deg] |
|---|---|---|---|---|
| Ziegler–Nichols PI | 2.304 | 6.44 | 1.90 | 30.1 |
| Skogestad SIMC (Tc = 2 s) | 1.571 | 16.00 | 3.09 | 57.3 |
| Skogestad SIMC (Tc = 10 s) | 0.524 | 22.00 | 9.42 | 80.5 |
| course material | 0.520 | 18.00 | 9.41 | 75.1 |

The task asks for the Ziegler–Nichols frequency response method specifically.
It is computed from the ultimate point of the process (`Ku = 5.12`,
`Pu = 7.73 s`), found analytically from the model — **don't run the ZN
experiment on the real rig to find `Ku`.** Driving a heater into sustained
oscillation at its limits is not something to provoke deliberately when the
model gives you the same number.

ZN is the aggressive choice here: fastest response, but it spends most of the
margin (GM 1.9 is thin). SIMC with `Tc = 10 s` reproduces the course values
almost exactly, which is a good hint at where those came from.

### What "unstable" actually looks like

Above `Kc` the loop does not diverge — the heater saturates at 0–5 V, so it
settles into a bounded limit cycle of about 0.9 °C peak to peak. That is the
real behaviour, and it is why an unstable loop on a real process looks like
steady hunting rather than an explosion. The test suite checks for exactly
this.

## Batch simulation and tuning sweeps

A feedback loop's time recursion cannot be vectorised, but the *parameter* axis
can: every candidate tuning advances one sample together, as numpy vector
operations. `demos/tuning_sweep.py` scores 1200 tunings on an IAE grid and
draws the stability boundary on top.

```python
from airheater.simulation.batch import simulate_closed_loop
res = simulate_closed_loop(28.0, Ts=0.1, duration=300.0,
                           Kp=np.linspace(0.1, 6.0, 40), Ti=18.0)
res.iae()        # one score per tuning
```

`simulate_open_loop` is different: the input sequence is known in advance, so
the forced response is a convolution and the whole run is computed in one step
rather than iterated.

Both are checked against the scalar simulator sample by sample in the tests, so
a tuning that looks good in a sweep behaves identically in the real-time loop.

## MQTT and the database application

```
control_app.py  --MQTT-->  broker  --MQTT-->  database_app.py  -->  SQLite
  (Raspberry Pi)                                 (collector)
```

The control applications never touch the database. They publish to a broker,
and one collector subscribes and writes. The task asks for this arrangement,
and the reasons are worth stating in the report: the Pi holds no database
credentials, the database is not exposed to the process network, several rigs
share one collector, and the broker buffers while the database is down instead
of the control loop blocking on a write.

### Topics

```
airheater/data/<source>          samples, QoS 1
airheater/data/<source>/status   "online" / "offline", retained
```

The status topic is the connection's **last will**: the broker publishes
`offline` by itself if the Pi loses power or the network drops. Without it, a
dead control application and an idle one look identical to the collector.

### Running it

Start a broker, then the collector, then the control application:

```bash
mosquitto -p 1883 -v
```

```bash
python apps/database_app.py --host 127.0.0.1 --no-tls --db airheater.db
```

```bash
python apps/control_app.py --mqtt-host 127.0.0.1 --no-tls --duration 300
```

```bash
python apps/database_app.py --stats --db airheater.db
```

Credentials come from the environment, never from the command line — an
argument is visible to every other process on the machine:

```bash
export AIRHEATER_MQTT_HOST=broker.example.com AIRHEATER_MQTT_USERNAME=collector AIRHEATER_MQTT_PASSWORD=...
```

`demos/mqtt_pipeline.py` runs the same chain with the broker replaced by an
in-process loopback, so you can see the data path working before any broker
exists.

## Cyber security notes

These are the decisions in the code, which is the part of the delivery that
asks what you did to protect the system.

**Transport.** TLS is on by default and the port follows it (8883/1883).
Turning it off is deliberate (`--no-tls`), and `MqttConfig.warnings()` prints
what you gave up. Sending a password over an unencrypted connection is
*refused*, not warned about: MQTT puts credentials in the CONNECT packet in
the clear. `tls_insecure` exists for a lab broker with a self-signed
certificate, and is described as what it is — encryption without
authentication.

**Credentials** are read from environment variables. None appear in source, and
`MqttConfig` keeps the password out of its `repr`, so a config object in a log
line or a traceback does not leak it.

**Input validation.** Anything arriving from a broker is data from the network:
any client allowed to publish on the topic can send what it likes. So
`decode_measurement` is the only way in, and it enforces a size limit before
parsing, JSON only (never `pickle` or `eval`, both of which execute the
payload), an exact set of fields and types, finite numbers — `json.loads`
accepts `NaN` and `Infinity`, and one `NaN` in the table poisons every average
computed over it — plausible physical ranges, and a constrained `source`
string, since that value reaches the database and the log. Rejected messages
are counted and dropped; one bad message must not stop the collector.

**SQL injection.** Every statement binds its parameters. Nothing is formatted
into SQL. A bound parameter travels to the database separately from the
statement text, so its contents can never be parsed as SQL — that is the whole
defence, and it is not about escaping quotes. `test_sql_injection_is_not_possible`
attacks the store directly with `'; DROP TABLE measurements;--` and three other
payloads, bypassing the validator, and asserts the table and every row survive.
Note placeholders only work for *values*: table and column names must come from
a fixed allowlist in your own code.

Defence in depth: the `source` validator rejects those same strings one layer
earlier, and the SQL Server notes recommend an account granted only
`INSERT, SELECT` on the one table — so even a successful injection against it
could not drop anything.

**Availability.** Telemetry must never be able to stop a heater's controller.
`MqttPublisher.publish` swallows and counts failures, and `connect` uses
paho's `connect_async`, not the blocking `connect` — the latter raises
`ConnectionRefusedError` inline when the broker is down, which would take the
control application with it. Two tests run the full loop against a dead broker
and assert it still reaches setpoint.

**Still to do:** broker ACLs so each rig may publish only on its own topic;
client certificates instead of passwords; and a retention policy for the
measurements table. Worth naming in the report as known gaps.

## Notes for the delivery

- Lowest IAE is not automatically the best tuning. Check the margins and the
  control signal activity before adopting what a sweep picks.
- `SqlServerStore` has **not** been tested against a server; `SqliteStore` is
  the one the test suite exercises. Check the schema and connection string
  against your own instance before relying on it.
- The MQTT path is verified end to end in process, and against a refused
  socket, but not against a live broker — there was none available when it was
  written. Run the `mosquitto` sequence above before recording the video.
