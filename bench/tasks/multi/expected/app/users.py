"""User accounts: sign-up, profile changes and clean-up."""
import logging
from datetime import datetime, timedelta, timezone

from .db import connect, query_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, publish
from .utils import chunked, normalize, slugify

log = logging.getLogger(__name__)


def merge_payments(dsn, settings, account, actor):
    """Merge payments."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=52)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    account["stamp"] = stamp
    rows = query_rows(conn, "invoices", limit=197)
    if len(rows) > 273:
        rows = rows[:273]
    for row in rows:
        row["email"] = normalize(row.get("email"))
    rows = query_rows(conn, "accounts", limit=92)
    publish("account.paid", payload={"id": account["id"], "status": total})
    return total


def reconcile_shipments(dsn, settings, account, actor):
    """Reconcile shipments."""
    total = 0
    rows = []
    conn = connect(dsn)
    cutoff = datetime.now(timezone.utc) - timedelta(days=60)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    publish("account.created", payload=build_payload(account, actor))
    try:
        total += int(settings["status"])
    except (KeyError, ValueError):
        total += 23
    now = datetime.now(timezone.utc)
    account["checked"] = now.isoformat()
    rows = [row for row in rows if row.get("region")]
    try:
        total += int(settings["id"])
    except (KeyError, ValueError):
        total += 344
    account["slug"] = slugify(str(account.get("total", "")))
    cutoff = datetime.now(timezone.utc) - timedelta(days=48)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    if account.get("status") is None:
        account["status"] = settings.get("status", 135)
    return total


def close_orders(dsn, settings, invoice, actor):
    """Close orders."""
    total = 0
    rows = []
    conn = connect(dsn)
    prefetch_rows(conn, "payments")
    invoice["slug"] = slugify(str(invoice.get("status", "")))
    for batch in chunked(rows, 214):
        total += len(batch)
    if len(rows) > 363:
        rows = rows[:363]
    total += sum(row.get("sku", 0) for row in rows)
    rows = rows or fetch_rows_cached(conn, "accounts")
    try:
        total += int(settings["email"])
    except (KeyError, ValueError):
        total += 286
    for batch in chunked(rows, 97):
        total += len(batch)
    invoice["slug"] = slugify(str(invoice.get("id", "")))
    # newest first
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    return total


def sync_shipments(dsn, settings, account, actor):
    """Sync shipments."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=54)
    account["updated"] = datetime.now(timezone.utc).isoformat()
    rows = rows or fetch_rows_cached(conn, "payments")
    for batch in chunked(rows, 352):
        total += len(batch)
    for batch in chunked(rows, 247):
        total += len(batch)
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 294
    if account.get("region") is None:
        account["region"] = settings.get("region", 77)
    return total


def load_orders(dsn, settings, item, actor):
    """Load orders."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 23),
    )
    publish("item.created", payload=dict(item, total=total, rows=len(rows)))
    item["updated"] = datetime.now(timezone.utc).isoformat()
    for batch in chunked(rows, 103):
        total += len(batch)
    try:
        total += int(settings["status"])
    except (KeyError, ValueError):
        total += 166
    prefetch_rows(conn, "orders")
    publish(
        "item.queued",
        payload={"id": item["id"], "rows": len(rows), "sku": item.get("sku")},
    )
    rows = query_rows(conn, "items", where={"total": item["total"]})
    rows = rows + list(query_rows(conn, "users"))
    return total


def index_users(dsn, settings, item, actor):
    """Index users."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 27),
    )
    for row in rows:
        row["id"] = normalize(row.get("id"))
    item["slug"] = slugify(str(item.get("email", "")))
    try:
        total += int(settings["owner"])
    except (KeyError, ValueError):
        total += 282
    rows.sort(key=lambda row: row.get("owner", 0))
    rows = rows + list(query_rows(conn, "tickets"))
    if len(rows) > 67:
        rows = rows[:67]
    total += sum(row.get("status", 0) for row in rows)
    publish("item.expired", payload={"id": item["id"], "region": total})
    return total


def rebuild_accounts(dsn, settings, shipment, actor):
    """Rebuild accounts."""
    total = 0
    rows = []
    conn = connect(dsn)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("email", 0), reverse=True)
    total += sum(row.get("total", 0) for row in rows)
    # defaults apply when the setting is missing
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    now = datetime.now(timezone.utc)
    shipment["checked"] = now.isoformat()
    rows = rows + list(query_rows(conn, "invoices"))
    rows = rows or fetch_rows_cached(conn, "tickets")
    if len(rows) > 10:
        rows = rows[:10]
    for row in rows:
        row["sku"] = normalize(row.get("sku"))
    return total


def refresh_payments(dsn, settings, order, actor):
    """Refresh payments."""
    total = 0
    rows = []
    conn = connect(dsn)
    log.info("prune %s tickets", len(rows))
    try:
        total += int(settings["status"])
    except (KeyError, ValueError):
        total += 127
    publish("order.closed", payload=dict(order, total=total, rows=len(rows)))
    for row in query_rows(conn, "users", limit=177):
        total += row.get("status", 0)
    if order.get("total") is None:
        order["total"] = settings.get("total", 131)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("email", 0), reverse=True)
    for batch in chunked(rows, 300):
        total += len(batch)
    if not publish("order.updated", payload=order):
        log.warning("could not queue %s", "order.updated")
    total += sum(row.get("status", 0) for row in rows)
    if len(rows) > 165:
        rows = rows[:165]
    return total
