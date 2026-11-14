"""Check receiver database access without printing credentials or raw errors."""
import os
from pathlib import Path
import sys


def error_hint(error):
    # Inspect locally, but never emit raw text: libpq errors can contain secrets.
    message = str(error).lower()
    if "password authentication failed" in message:
        return "Password authentication failed. Check ingest password and role login."
    if "root certificate file" in message or "sslrootcert" in message:
        return "TLS root certificate file missing or invalid. Check sslrootcert path on VM."
    if "certificate verify failed" in message or "does not match host name" in message:
        return "TLS certificate verification failed. Check CA certificates and Azure hostname."
    if "could not translate host name" in message or "name or service not known" in message or "failed to resolve" in message:
        return "DNS lookup failed. Check database hostname and VM DNS."
    if "no pg_hba.conf entry" in message:
        return "Connection rejected. Check Azure firewall rule for VM public IP and TLS settings."
    if "timeout" in message or "timed out" in message:
        return "Connection timed out. Check Azure firewall, VM outbound network and server Ready status."
    if "connection refused" in message:
        return "Connection refused. Check database hostname, port 5432 and server Ready status."
    if "permission denied" in message:
        return "Access denied. Check database/schema/table grants and certificate file permissions."
    if "does not exist" in message:
        return "Database, role or table missing. Check database name and initialization scripts."
    return "Unclassified error. Raw error withheld to protect connection credentials."


def main():
    try:
        from dotenv import load_dotenv
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        load_dotenv(Path(__file__).resolve().parents[1] / ".env",
                    override=False, interpolate=False)
        dsn = os.environ.get("AIRHEATER_POSTGRES_DSN", "")
        if not dsn:
            print("Missing AIRHEATER_POSTGRES_DSN in environment or repository .env")
            return 1
        settings = conninfo_to_dict(dsn)
        print("Checking PostgreSQL connection (credentials hidden)...", flush=True)
        if settings.get("sslmode") != "verify-full":
            print("Expected sslmode=verify-full. Update the private DSN before testing.")
            return 1
        with psycopg.connect(dsn, connect_timeout=10,
                             options="-c statement_timeout=15000") as connection:
            print("PostgreSQL connection: OK", flush=True)
            for table in ("simulation_measurements", "receiver_checkpoints"):
                # Fixed statements only, never SQL built from remote input.
                statement = {
                    "simulation_measurements": "SELECT 1 FROM public.simulation_measurements LIMIT 1",
                    "receiver_checkpoints": "SELECT 1 FROM public.receiver_checkpoints LIMIT 1",
                }[table]
                connection.execute(statement)
                print("Read access to {}: OK".format(table), flush=True)
            encrypted = connection.execute(
                "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
            print("TLS active: {}".format(bool(encrypted and encrypted[0])))
        print("Database connection and read checks passed; no data modified.")
        return 0
    except Exception as error:
        print("PostgreSQL check failed ({})".format(type(error).__name__))
        print(error_hint(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
