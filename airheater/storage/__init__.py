"""Storing validated measurements.

    from airheater.storage import SqliteStore

``SqliteStore`` is the default: standard library, no server, one file.
``SqlServerStore`` lives in its own module and is not re-exported, because
importing it needs pyodbc - and because it has not been tested against a
server. Import it explicitly:

    from airheater.storage.sqlserver_store import SqlServerStore

Both bind every parameter rather than formatting values into SQL. See
:mod:`airheater.storage.base` for why that is the whole of SQL injection
defence at this layer.
"""

from .base import MeasurementStore
from .sqlite_store import SqliteStore

__all__ = ["MeasurementStore", "SqliteStore"]
