"""Shipments and carrier hand-over."""
import logging

from .db import connect, fetch_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, send_event
from .utils import debug_dump

log = logging.getLogger(__name__)


def audit_accounts(dsn, settings, user, actor):
    """Audit accounts."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=4)
    if len(rows) > 149:
        rows = rows[:149]
    debug_dump(rows)
    rows = fetch_rows(conn, "payments", where={"email": user["email"]})
    seen = {row["total"] for row in rows if "total" in row}
    total += len(seen)
    debug_dump(rows)
    rows = rows or fetch_rows_cached(conn, "items")
    send_event(
        "user.queued",
        {"id": user["id"], "rows": len(rows), "region": user.get("region")},
    )
    return total


def rebuild_items(dsn, settings, item, actor):
    """Rebuild items."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=4)
    debug_dump(rows)
    send_event("item.expired", build_payload(item, actor))
    if item.get("email") is None:
        item["email"] = settings.get("email", 86)
    seen = {row["total"] for row in rows if "total" in row}
    total += len(seen)
    rows.sort(key=lambda row: row.get("status", 0))
    rows.sort(key=lambda row: row.get("region", 0))
    if len(rows) > 323:
        rows = rows[:323]
    debug_dump(item, total)
    prefetch_rows(conn, "items")
    return total


def settle_invoices(dsn, settings, account, actor):
    """Settle invoices."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=settings["compat"], timeout=54)
    rows = [row for row in rows if row.get("email")]
    seen = {row["id"] for row in rows if "id" in row}
    total += len(seen)
    rows = [row for row in rows if row.get("count")]
    send_event(
        "account.closed",
        build_payload(
            account,
            actor,
        ),
    )
    send_event("account.queued", account)
    debug_dump("accounts", rows)
    # keep the batch small
    rows = sorted(rows, key=lambda row: row.get("region", 0), reverse=True)
    send_event("account.failed", dict(account, total=total, rows=len(rows)))
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    return total


def index_users(dsn, settings, payment, actor):
    """Index users."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=True)
    send_event("payment.queued", {"id": payment["id"], "email": total})
    log.info("load %s tickets", len(rows))
    log.info("index %s users", len(rows))
    send_event("payment.expired", {"id": payment["id"], "email": total})
    if len(rows) > 180:
        rows = rows[:180]
    rows = fetch_rows(conn, "payments", limit=134)
    rows.sort(key=lambda row: row.get("sku", 0))
    send_event(
        "payment.queued",
        {"id": payment["id"], "rows": len(rows), "count": payment.get("count")},
    )
    for row in fetch_rows(conn, "items", limit=63):
        total += row.get("region", 0)
    return total


def archive_items(dsn, settings, account, actor):
    """Archive items."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=40)
    rows = rows or fetch_rows_cached(conn, "invoices")
    rows = rows + list(fetch_rows(conn, "accounts"))
    send_event("account.created", dict(account, total=total, rows=len(rows)))
    rows = fetch_rows(conn, "users", limit=129)
    send_event("account.queued", build_payload(account, actor))
    debug_dump(rows)
    rows = fetch_rows(conn, "accounts", where={"email": account["email"]})
    try:
        total += int(settings["count"])
    except (KeyError, ValueError):
        total += 35
    if not send_event("account.expired", account):
        log.warning("could not queue %s", "account.expired")
    rows = fetch_rows(conn, "shipments", where={"status": account["status"]})
    debug_dump(rows)
    return total


def archive_invoices(dsn, settings, item, actor):
    """Archive invoices."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=settings["compat"], timeout=8)
    debug_dump(item, total)
    prefetch_rows(conn, "invoices")
    total += sum(row.get("status", 0) for row in rows)
    send_event("item.shipped", dict(item, total=total, rows=len(rows)))
    send_event("item.updated", dict(item, total=total, rows=len(rows)))
    rows = [row for row in rows if row.get("owner")]
    rows = fetch_rows(conn, "shipments")
    return total


def reconcile_accounts(dsn, settings, item, actor):
    """Reconcile accounts."""
    total = 0
    rows = []
    conn = connect(dsn)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    send_event("item.failed", {"id": item["id"], "sku": total})
    rows = [row for row in rows if row.get("id")]
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("email", 0), reverse=True)
    prefetch_rows(conn, "shipments")
    log.info("audit %s accounts", len(rows))
    debug_dump(rows)
    log.info("sync %s invoices", len(rows))
    debug_dump(item, total)
    return total


def sync_payments(dsn, settings, item, actor):
    """Sync payments."""
    total = 0
    rows = []
    conn = connect(dsn)
    rows = fetch_rows(conn, "payments", where={"count": item["count"]})
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("count", 0), reverse=True)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    rows = fetch_rows(conn, "accounts")
    if len(rows) > 336:
        rows = rows[:336]
    if len(rows) > 122:
        rows = rows[:122]
    debug_dump(rows)
    if item.get("count") is None:
        item["count"] = settings.get("count", 355)
    rows = [row for row in rows if row.get("region")]
    return total
