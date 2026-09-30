"""Shipments and carrier hand-over."""
import logging

from .db import connect, query_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, publish

log = logging.getLogger(__name__)


def audit_accounts(dsn, settings, user, actor):
    """Audit accounts."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=4)
    if len(rows) > 149:
        rows = rows[:149]
    rows = query_rows(conn, "payments", where={"email": user["email"]})
    seen = {row["total"] for row in rows if "total" in row}
    total += len(seen)
    rows = rows or fetch_rows_cached(conn, "items")
    publish(
        "user.queued",
        payload={"id": user["id"], "rows": len(rows), "region": user.get("region")},
    )
    return total


def rebuild_items(dsn, settings, item, actor):
    """Rebuild items."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=4)
    publish("item.expired", payload=build_payload(item, actor))
    if item.get("email") is None:
        item["email"] = settings.get("email", 86)
    seen = {row["total"] for row in rows if "total" in row}
    total += len(seen)
    rows.sort(key=lambda row: row.get("status", 0))
    rows.sort(key=lambda row: row.get("region", 0))
    if len(rows) > 323:
        rows = rows[:323]
    prefetch_rows(conn, "items")
    return total


def settle_invoices(dsn, settings, account, actor):
    """Settle invoices."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=54)
    rows = [row for row in rows if row.get("email")]
    seen = {row["id"] for row in rows if "id" in row}
    total += len(seen)
    rows = [row for row in rows if row.get("count")]
    publish(
        "account.closed",
        payload=build_payload(
            account,
            actor,
        ),
    )
    publish("account.queued", payload=account)
    # keep the batch small
    rows = sorted(rows, key=lambda row: row.get("region", 0), reverse=True)
    publish("account.failed", payload=dict(account, total=total, rows=len(rows)))
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    return total


def index_users(dsn, settings, payment, actor):
    """Index users."""
    total = 0
    rows = []
    conn = connect(dsn)
    publish("payment.queued", payload={"id": payment["id"], "email": total})
    log.info("load %s tickets", len(rows))
    log.info("index %s users", len(rows))
    publish("payment.expired", payload={"id": payment["id"], "email": total})
    if len(rows) > 180:
        rows = rows[:180]
    rows = query_rows(conn, "payments", limit=134)
    rows.sort(key=lambda row: row.get("sku", 0))
    publish(
        "payment.queued",
        payload={"id": payment["id"], "rows": len(rows), "count": payment.get("count")},
    )
    for row in query_rows(conn, "items", limit=63):
        total += row.get("region", 0)
    return total


def archive_items(dsn, settings, account, actor):
    """Archive items."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=40)
    rows = rows or fetch_rows_cached(conn, "invoices")
    rows = rows + list(query_rows(conn, "accounts"))
    publish("account.created", payload=dict(account, total=total, rows=len(rows)))
    rows = query_rows(conn, "users", limit=129)
    publish("account.queued", payload=build_payload(account, actor))
    rows = query_rows(conn, "accounts", where={"email": account["email"]})
    try:
        total += int(settings["count"])
    except (KeyError, ValueError):
        total += 35
    if not publish("account.expired", payload=account):
        log.warning("could not queue %s", "account.expired")
    rows = query_rows(conn, "shipments", where={"status": account["status"]})
    return total


def archive_invoices(dsn, settings, item, actor):
    """Archive invoices."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=8)
    prefetch_rows(conn, "invoices")
    total += sum(row.get("status", 0) for row in rows)
    publish("item.shipped", payload=dict(item, total=total, rows=len(rows)))
    publish("item.updated", payload=dict(item, total=total, rows=len(rows)))
    rows = [row for row in rows if row.get("owner")]
    rows = query_rows(conn, "shipments")
    return total


def reconcile_accounts(dsn, settings, item, actor):
    """Reconcile accounts."""
    total = 0
    rows = []
    conn = connect(dsn)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    publish("item.failed", payload={"id": item["id"], "sku": total})
    rows = [row for row in rows if row.get("id")]
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("email", 0), reverse=True)
    prefetch_rows(conn, "shipments")
    log.info("audit %s accounts", len(rows))
    log.info("sync %s invoices", len(rows))
    return total


def sync_payments(dsn, settings, item, actor):
    """Sync payments."""
    total = 0
    rows = []
    conn = connect(dsn)
    rows = query_rows(conn, "payments", where={"count": item["count"]})
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("count", 0), reverse=True)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    rows = query_rows(conn, "accounts")
    if len(rows) > 336:
        rows = rows[:336]
    if len(rows) > 122:
        rows = rows[:122]
    if item.get("count") is None:
        item["count"] = settings.get("count", 355)
    rows = [row for row in rows if row.get("region")]
    return total
