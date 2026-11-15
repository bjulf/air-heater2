"""Loopback-only command API. Use a TLS reverse proxy for Grafana access."""
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.telemetry.command_api import IoTHubCommands, ControlServer


def main():
    logging.disable(logging.CRITICAL)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1]/'.env', override=False, interpolate=False)
        commands = IoTHubCommands(os.environ['IOTHUB_SERVICE_CONNECTION_STRING'],
                                 os.environ.get('AIRHEATER_CONTROL_DEVICE_ID', 'airheater-sim-1'))
        token = os.environ['AIRHEATER_CONTROL_TOKEN'].strip()
        origin = os.environ['AIRHEATER_GRAFANA_ORIGIN'].strip()
        with ControlServer(('127.0.0.1', 8765), commands, token, origin) as server:
            print('Control API on 127.0.0.1:8765; use TLS proxy for Grafana', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print('Control API stopped ({}). Check private settings and port 8765.'.format(
              type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
