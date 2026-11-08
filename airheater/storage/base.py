"""The interface the database application stores measurements through.

Same idea as :mod:`airheater.interfaces` on the process side: one small
interface, so the collector does not care which database is behind it and
swapping SQLite for SQL Server is a one-line change.

On SQL injection
----------------
Every implementation must build statements with **placeholders and bound
parameters**, never with string formatting. The rule is absolute and it is not
about escaping quotes: a bound parameter is sent to the database separately
from the statement text, so the value can never be parsed as SQL no matter
what it contains. Formatting a value into the statement puts attacker-chosen
text where the parser will read it.

    # Correct - the value is data, whatever it contains
    cur.execute("SELECT * FROM measurements WHERE source = ?", (source,))

    # Wrong - the value becomes part of the statement
    cur.execute("SELECT * FROM measurements WHERE source = '%s'" % source)

The second form is what ``test_sql_injection_is_not_possible`` in the test
suite attacks, with a source name containing ``'; DROP TABLE measurements;--``.

Note that placeholders only work for *values*. Table and column names cannot
be bound, so anything that selects them must come from a fixed allowlist in
your own code - never from a message, a request or a user.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, List, Optional

from ..telemetry.payload import Measurement

__all__ = ["MeasurementStore"]


class MeasurementStore(ABC):
    """Somewhere to put validated measurements."""

    @abstractmethod
    def store(self, measurement: Measurement) -> bool:
        """Store one measurement.

        Returns True if a row was written, False if it was a duplicate. QoS 1
        delivers at least once, so duplicates are expected and are not errors.
        """

    def store_many(self, measurements: Iterable[Measurement]) -> int:
        """Store several measurements. Returns the number of new rows."""
        return sum(1 for m in measurements if self.store(m))

    @abstractmethod
    def recent(self, source: Optional[str] = None, limit: int = 100) -> List[dict]:
        """Most recent measurements, newest first."""

    @abstractmethod
    def count(self, source: Optional[str] = None) -> int:
        """Number of stored measurements."""

    def close(self) -> None:
        """Release the connection. Override where that means something."""

    def __enter__(self) -> "MeasurementStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
