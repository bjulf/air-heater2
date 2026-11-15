"""Send a manual setpoint through the local VM API, without printing credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('setpoint', type=float)
    args = p.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from airheater.control.setpoint import validate_setpoint
    try:
        value = validate_setpoint({'setpoint_c': args.setpoint})
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1]/'.env', override=False, interpolate=False)
        token = os.environ['AIRHEATER_CONTROL_TOKEN'].strip()
        request = Request('http://127.0.0.1:8765/api/setpoint',
            data=json.dumps({'setpoint_c': value}).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
        try:
            with urlopen(request, timeout=15) as response:
                print(response.read(4096).decode())
            return 0
        except HTTPError as exc:
            print('HTTP {}: {}'.format(exc.code, exc.read(4096).decode()), file=sys.stderr)
            return 1
    except Exception as exc:
        print('Setpoint check failed ({})'.format(type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
