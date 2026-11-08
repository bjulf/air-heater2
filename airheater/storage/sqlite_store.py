"""SQLite implementation of :class:`~airheater.storage.base.MeasurementStore`.

SQLite is in the standard library, needs no server and keeps the database in a
single file, so the collector runs anywhere - including on the Raspberry Pi
itself if you want local buffering. The task allows "another Database System
if you prefer"; see :mod:`airheater.storage.sqlserver_store` for the SQL Server
variant, which uses the same interface and the same parameter binding.

Every statement here binds its values. Nothing is formatted into SQL.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import List, Optional

from ..telemetry.payload import Measurement
from .base import MeasurementStore

__all__ = ["SqliteStore"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    k           INTEGER NOT NULL,
    t           REAL    NOT NULL,
    setpoint    REAL    NOT NULL,
    measured    REAL    NOT NULL,
    filtered    REAL    NOT NULL,
    error       REAL    NOT NULL,
    u           REAL    NOT NULL,
    saturated   INTEGER NOT NULL,
    jitter      REAL    NOT NULL,
    schema      INTEGER NOT NULL,
    received_at REAL    NOT NULL,
    -- QoS 1 delivers at least once, so the same sample can arrive twice.
    -- The database settles it rather than the application guessing.
    UNIQUE (source, k)
);
CREATE INDEX IF NOT EXISTS idx_measurements_source_time
    ON measurements (source, received_at);
"""

_INSERT = """
INSERT INTO measurements
    (source, k, t, setpoint, measured, filtered, error, u, saturated,
     jitter, schema, received_at)
VALUES
    (:source, :k, :t, :setpoint, :measured, :filtered, :error, :u, :saturated,
     :jitter, :schema, :received_at)
"""


class SqliteStore(MeasurementStore):
    """Store measurements in a SQLite database file.

    Parameters
    ----------
    path:
        Database file. ``":memory:"`` gives a throwaway database, which is
        what the tests use.
    commit_every:
        Commit after this many rows. One commit per row is an fsync per
        sample, which is wasteful at 10 Hz and wears an SD card; batching
        trades a few seconds of data on a hard crash for much less I/O.
        Use 1 if you cannot afford to lose anything.
    """

    def __init__(self, path: str = "airheater.db", commit_every: int = 20) -> None:
        if commit_every < 1:
            raise ValueError("commit_every must be >= 1")
        self.path = path
        self.commit_every = int(commit_every)
        self._pending = 0
        # The subscriber calls store() from paho's network thread while the
        # main thread may be reading; one lock keeps that honest.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(SCHEMA)

    def _require_open(self) -> None:
        if self._conn is None:
            raise RuntimeError("store is closed")

    def store(self, measurement: Measurement) -> bool:
        row = measurement.as_row()
        with self._lock:
            self._require_open()
            try:
                # Named placeholders: every value is bound, none is formatted in.
                self._conn.execute(_INSERT, row)
            except sqlite3.IntegrityError:
                # Same (source, k) already stored - a QoS 1 redelivery.
                return False
            self._pending += 1
            if self._pending >= self.commit_every:
                self._conn.commit()
                self._pending = 0
            return True

    def flush(self) -> None:
        """Commit whatever is pending."""
        with self._lock:
            if self._pending:
                self._conn.commit()
                self._pending = 0

    def recent(self, source: Optional[str] = None, limit: int = 100) -> List[dict]:
        limit = max(1, min(int(limit), 10000))
        with self._lock:
            self._require_open()
            # The LIMIT is bound too, and clamped above, so a caller cannot ask
            # for the whole table by accident.
            if source is None:
                cur = self._conn.execute(
                    "SELECT * FROM measurements ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM measurements WHERE source = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (source, limit),
                )
            return [dict(r) for r in cur.fetchall()]

    def count(self, source: Optional[str] = None) -> int:
        with self._lock:
            self._require_open()
            if source is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM measurements")
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM measurements WHERE source = ?", (source,)
                )
            return int(cur.fetchone()[0])

    def sources(self) -> List[str]:
        with self._lock:
            self._require_open()
            cur = self._conn.execute(
                "SELECT DISTINCT source FROM measurements ORDER BY source"
            )
            return [r[0] for r in cur.fetchall()]

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
