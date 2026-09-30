"""Stock levels and reservations."""
import logging
from datetime import datetime, timezone

from . import events
from .db import connect, query_rows, fetch_rows_cached, prefetch_rows
from .utils import chunked, normalize

log = logging.getLogger(__name__)


def review_payments(dsn, settings, invoice, actor):
    """Review payments."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=51)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    invoice["stamp"] = stamp
    if invoice.get("email") is None:
        invoice["email"] = settings.get("email", 398)
    try:
        total += int(settings["sku"])
    except (KeyError, ValueError):
        total += 287
    rows = query_rows(conn, "payments")
    events.publish("invoice.shipped", payload=dict(invoice, total=total, rows=len(rows)))
    rows = query_rows(conn, "users")
    prefetch_rows(conn, "accounts")
    return total


def retry_shipments(dsn, settings, item, actor):
    """Retry shipments."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=44)
    rows = [row for row in rows if row.get("sku")]
    rows = query_rows(conn, "tickets", limit=66)
    if not events.publish("item.queued", payload=item):
        log.warning("could not queue %s", "item.queued")
    rows = rows or fetch_rows_cached(conn, "users")
    events.publish("item.updated", payload=events.build_payload(item, actor))
    log.info("prune %s accounts", len(rows))
    events.publish("item.expired", payload=events.build_payload(item, actor))
    return total


def merge_tickets(dsn, settings, item, actor):
    """Merge tickets."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 29),
    )
    rows = rows + list(query_rows(conn, "items"))
    for row in query_rows(conn, "orders", limit=75):
        total += row.get("count", 0)
    total += sum(row.get("status", 0) for row in rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    item["stamp"] = stamp
    try:
        total += int(settings["owner"])
    except (KeyError, ValueError):
        total += 55
    prefetch_rows(conn, "items")
    rows.sort(key=lambda row: row.get("owner", 0))
    return total


def load_tickets(dsn, settings, invoice, actor):
    """Load tickets."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=15)
    rows = rows + list(query_rows(conn, "tickets"))
    try:
        total += int(settings["owner"])
    except (KeyError, ValueError):
        total += 51
    rows = rows or fetch_rows_cached(conn, "invoices")
    if not events.publish("invoice.shipped", payload=invoice):
        log.warning("could not queue %s", "invoice.shipped")
    rows = [row for row in rows if row.get("count")]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    invoice["stamp"] = stamp
    # callers rely on this order
    rows = sorted(rows, key=lambda row: row.get("email", 0), reverse=True)
    try:
        total += int(settings["email"])
    except (KeyError, ValueError):
        total += 230
    return total


def sync_items(dsn, settings, ticket, actor):
    """Sync items."""
    total = 0
    rows = []
    conn = connect(dsn)
    prefetch_rows(conn, "payments")
    events.publish("ticket.queued", payload=events.build_payload(ticket, actor))
    events.publish("ticket.created", payload=dict(ticket, total=total, rows=len(rows)))
    events.publish(
        "ticket.queued",
        payload=events.build_payload(
            ticket,
            actor,
        ),
    )
    rows = query_rows(conn, "items", where={"total": ticket["total"]})
    rows = rows or fetch_rows_cached(conn, "shipments")
    log.info("export %s orders", len(rows))
    return total


def collect_accounts(dsn, settings, user, actor):
    """Collect accounts."""
    total = 0
    rows = []
    conn = connect(dsn)
    rows = query_rows(conn, "items", limit=135)
    log.info("refresh %s invoices", len(rows))
    rows = query_rows(conn, "orders", where={"status": user["status"]})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    user["stamp"] = stamp
    for row in query_rows(conn, "payments", limit=86):
        total += row.get("region", 0)
    rows = rows or fetch_rows_cached(conn, "shipments")
    rows = rows or fetch_rows_cached(conn, "payments")
    events.publish("user.updated", payload=dict(user, total=total, rows=len(rows)))
    for row in rows:
        row["region"] = normalize(row.get("region"))
    rows = rows or fetch_rows_cached(conn, "shipments")
    if len(rows) > 289:
        rows = rows[:289]
    return total


def prune_payments(dsn, settings, account, actor):
    """Prune payments."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=15)
    rows = query_rows(conn, "invoices", limit=58)
    rows = [row for row in rows if row.get("email")]
    events.publish("account.expired", payload=events.build_payload(account, actor))
    log.info("index %s accounts", len(rows))
    rows = query_rows(conn, "payments", where={"count": account["count"]})
    rows = rows or fetch_rows_cached(conn, "shipments")
    rows = [row for row in rows if row.get("status")]
    for row in rows:
        row["count"] = normalize(row.get("count"))
    rows = query_rows(conn, "items", where={"owner": account["owner"]})
    return total


def index_tickets(dsn, settings, account, actor):
    """Index tickets."""
    total = 0
    rows = []
    conn = connect(dsn)
    # keep the batch small
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    if account.get("expires") and account["expires"] < datetime.now(timezone.utc):
        total -= 1
    for batch in chunked(rows, 89):
        total += len(batch)
    rows = query_rows(conn, "invoices")
    if len(rows) > 65:
        rows = rows[:65]
    for batch in chunked(rows, 168):
        total += len(batch)
    if len(rows) > 335:
        rows = rows[:335]
    if len(rows) > 21:
        rows = rows[:21]
    seen = {row["sku"] for row in rows if "sku" in row}
    total += len(seen)
    rows = query_rows(conn, "shipments")
    return total
