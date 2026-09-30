"""Database access: a connection pool and row queries."""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_POOL = {}
_CACHE = {}


def connect(dsn, timeout=30):
    """Open a connection, or reuse a pooled one."""
    key = (dsn, timeout)
    if key not in _POOL:
        _POOL[key] = {"dsn": dsn, "timeout": timeout, "opened": datetime.now(timezone.utc), "tables": {}}
    return _POOL[key]


def query_rows(conn, table, where=None, limit=None):
    """Return the rows of a table as dictionaries."""
    rows = list(conn["tables"].get(table, []))
    if where:
        rows = [row for row in rows if all(row.get(key) == value for key, value in where.items())]
    if limit is not None:
        rows = rows[:limit]
    log.debug("%s rows from %s", len(rows), table)
    return rows


def fetch_rows_cached(conn, table):
    """Rows of a table, read once per connection."""
    key = (conn["dsn"], table)
    if key not in _CACHE:
        _CACHE[key] = query_rows(conn, table)
    return _CACHE[key]


def prefetch_rows(conn, table):
    """Warm the cache for a table."""
    fetch_rows_cached(conn, table)


def count_rows(conn, table, where=None):
    """How many rows of a table match."""
    return len(query_rows(conn, table, where=where))


def close_all():
    """Drop every pooled connection and the cache."""
    stale = [key for key, conn in _POOL.items() if (datetime.now(timezone.utc) - conn["opened"]).days >= 1]
    for key in stale:
        del _POOL[key]
    _CACHE.clear()
    return len(stale)
