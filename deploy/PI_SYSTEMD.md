# Pi publisher service

For the existing Pi account `bjulf`, checkout `/home/bjulf/air-heater2`, and
Python environment `.venv-azure`. This runs the control simulation with remote
setpoint commands enabled and publishes every 15 seconds. On every start the
initial setpoint is 28 C; a previous remote setpoint is not persisted.

Stop the manually running publisher with Ctrl+C first. Only one publisher may
use the device and its sequence counter at a time. Check for existing processes:

```bash
pgrep -afu bjulf '[a]pps/.*publisher.py'
```

Then install on the Pi:

```bash
cd /home/bjulf/air-heater2
git pull --ff-only
chmod 600 .env
sudo install -m 644 deploy/airheater-publisher.service /etc/systemd/system/airheater-publisher.service
sudo systemd-analyze verify /etc/systemd/system/airheater-publisher.service
sudo systemctl daemon-reload
sudo systemctl enable --now airheater-publisher
systemctl status airheater-publisher --no-pager
journalctl -u airheater-publisher -n 40 --no-pager
```

The application loads the device connection string from the existing `.env`.
No shell activation or systemd EnvironmentFile is needed. Do not put the VM's
IoT Hub service connection string on the Pi.

```bash
sudo systemctl stop airheater-publisher
sudo systemctl start airheater-publisher
sudo systemctl restart airheater-publisher
journalctl -u airheater-publisher -f
```

Ctrl+C exits the log viewer without stopping the service. Confirm live operation
by sending a setpoint from Grafana and checking `applied` and incoming telemetry;
an active service alone does not prove Azure delivery.

After a forced termination or power loss, the publisher may leave a sequence
lock. If the log reports FileExistsError, stop the service and verify that no
publisher process is running before removing only
`.state/airheater-sim-1.sequence.lock`. Keep `.state/airheater-sim-1.sequence`.
Never automatically remove the lock in the service unit.

For updates, stop the service, pull the code, and start the service. If the unit
file changes, install it again and run daemon-reload before starting.
