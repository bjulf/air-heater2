"""SQL Server implementation of :class:`~airheater.storage.base.MeasurementStore`.

**Not tested against a server.** The SQLite store is the one the test suite
exercises; this module is the same logic expressed in T-SQL and pyodbc, for
the case where the delivery calls for SQL Server. Check the schema and the
connection string against your own instance before relying on it.

Parameter binding is identical in spirit to the SQLite store: pyodbc uses
positional ``?`` placeholders, and no value is ever formatted into the
statement text.

Connection string
-----------------
Read from the environment, never written in source::

    set AIRHEATER_SQLSERVER_DSN=Driver={ODBC Driver 18 for SQL Server};Server=tcp:myserver.database.windows.net,1433;Database=airheater;Encrypt=yes;TrustServerCertificate=no
    set AIRHEATER_SQLSERVER_USER=airheater_writer
    set AIRHEATER_SQLSERVER_PASSWORD=...

Two settings in that string matter for security. ``Encrypt=yes`` turns on TLS
for the database connection; ``TrustServerCertificate=no`` makes the client
actually verify the server certificate, which is what stops an interceptor
presenting its own. Azure SQL requires both.

Least privilege
---------------
The collector only ever inserts and reads its own table, so give it an account
that can do only that - not ``db_owner``, and not the account the web
application uses::

    CREATE LOGIN airheater_writer WITH PASSWORD = '...';
    CREATE USER  airheater_writer FOR LOGIN airheater_writer;
    GRANT INSERT, SELECT ON dbo.measurements TO airheater_writer;

If the collector is ever compromised, that grant is the limit of what the
attacker inherits. It is also the reason a successful SQL injection against a
least-privileged account cannot drop the table.
"""

from __future__ import annotations

import os
import threading
from typing import List, Optional

from ..telemetry.payload import Measurement
from .base import MeasurementStore

__all__ = ["SqlServerStore"]

SCHEMA = """
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='measurements' AND xtype='U')
CREATE TABLE measurements (
    id          BIGINT IDENTITY(1,1) PRIMARY KEY,
    source      NVARCHAR(64)  NOT NULL,
    k           BIGINT        NOT NULL,
    t           FLOAT         NOT NULL,
    setpoint    FLOAT         NOT NULL,
    measured    FLOAT         NOT NULL,
    filtered    FLOAT         NOT NULL,
    error       FLOAT         NOT NULL,
    u           FLOAT         NOT NULL,
    saturated   BIT           NOT NULL,
    jitter      FLOAT         NOT NULL,
    [schema]    INT           NOT NULL,
    received_at FLOAT         NOT NULL,
    CONSTRAINT uq_measurements_source_k UNIQUE (source, k)
);
"""

_INSERT = """
INSERT INTO measurements
    (source, k, t, setpoint, measured, filtered, error, u, saturated,
     jitter, [schema], received_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
"""

_COLUMNS = (
    "source", "k", "t", "setpoint", "measured", "filtered", "error", "u",
    "saturated", "jitter", "schema", "received_at",
)


class SqlServerStore(MeasurementStore):
    """Store measurements in SQL Server through pyodbc.

    Parameters
    ----------
    dsn:
        ODBC connection string. Defaults to ``AIRHEATER_SQLSERVER_DSN``.
    username, password:
        Default to ``AIRHEATER_SQLSERVER_USER`` / ``_PASSWORD``. Omit both to
        use the connection string's own authentication, e.g. Windows
        authentication or a managed identity - which is better still, since
        then there is no password to leak.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        commit_every: int = 20,
    ) -> None:
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise ImportError(
                "pyodbc is required for SqlServerStore: pip install pyodbc "
                "(and install the Microsoft ODBC Driver for SQL Server). "
                "Use SqliteStore to work without a server."
            ) from exc

        dsn = dsn or os.environ.get("AIRHEATER_SQLSERVER_DSN")
        if not dsn:
            raise ValueError(
                "no connection string: pass dsn= or set AIRHEATER_SQLSERVER_DSN"
            )
        username = username or os.environ.get("AIRHEATER_SQLSERVER_USER")
        password = password or os.environ.get("AIRHEATER_SQLSERVER_PASSWORD")

        kwargs = {}
        if username:
            # Passed as connection keywords, not concatenated into the string,
            # so a password containing ';' or '}' cannot alter the other
            # settings - the same injection risk as in SQL, one layer out.
            kwargs["UID"] = username
            kwargs["PWD"] = password

        self.commit_every = int(commit_every)
        self._pending = 0
        self._lock = threading.Lock()
        self._conn = pyodbc.connect(dsn, autocommit=False, **kwargs)
        with self._lock:
            self._conn.execute(SCHEMA)
            self._conn.commit()

    def store(self, measurement: Measurement) -> bool:
        import pyodbc

        row = measurement.as_row()
        values = [row[c] for c in _COLUMNS]
        with self._lock:
            try:
                self._conn.execute(_INSERT, values)
            except pyodbc.IntegrityError:
                return False        # QoS 1 redelivery
            self._pending += 1
            if self._pending >= self.commit_every:
                self._conn.commit()
                self._pending = 0
            return True

    def recent(self, source: Optional[str] = None, limit: int = 100) -> List[dict]:
        limit = max(1, min(int(limit), 10000))
        with self._lock:
            if source is None:
                cur = self._conn.execute(
                    "SELECT TOP (?) * FROM measurements ORDER BY id DESC", (limit,)
                )
            else:
                cur = self._conn.execute(
                    "SELECT TOP (?) * FROM measurements WHERE source = ? "
                    "ORDER BY id DESC",
                    (limit, source),
                )
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, r)) for r in cur.fetchall()]

    def count(self, source: Optional[str] = None) -> int:
        with self._lock:
            if source is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM measurements")
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM measurements WHERE source = ?", (source,)
                )
            return int(cur.fetchone()[0])

    def flush(self) -> None:
        with self._lock:
            if self._pending:
                self._conn.commit()
                self._pending = 0

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                if self._pending:
                    self._conn.commit()
            finally:
                self._conn.close()
                self._conn = None
