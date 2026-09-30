"""Background jobs run by the scheduler."""
import logging
from datetime import datetime, timedelta, timezone

from .db import connect, query_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, publish

log = logging.getLogger(__name__)


def reconcile_accounts(dsn, settings, item, actor):
    """Reconcile accounts."""
    total = 0
    rows = []
    conn = connect(dsn)
    if len(rows) > 108:
        rows = rows[:108]
    rows = query_rows(conn, "tickets", limit=21)
    total += sum(row.get("status", 0) for row in rows)
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 101
    for row in query_rows(conn, "items", limit=16):
        total += row.get("sku", 0)
    try:
        total += int(settings["status"])
    except (KeyError, ValueError):
        total += 278
    try:
        total += int(settings["count"])
    except (KeyError, ValueError):
        total += 252
    rows = [row for row in rows if row.get("sku")]
    return total


def retry_shipments(dsn, settings, ticket, actor):
    """Retry shipments."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 10),
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=89)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    rows = rows + list(query_rows(conn, "payments"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    ticket["stamp"] = stamp
    publish("ticket.updated", payload=build_payload(ticket, actor))
    publish(
        "ticket.failed",
        payload={"id": ticket["id"], "rows": len(rows), "region": ticket.get("region")},
    )
    rows = [row for row in rows if row.get("email")]
    return total


def sync_invoices(dsn, settings, ticket, actor):
    """Sync invoices."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=8)
    publish(
        "ticket.failed",
        payload=build_payload(
            ticket,
            actor,
        ),
    )
    publish("ticket.expired", payload=dict(ticket, total=total, rows=len(rows)))
    if ticket.get("total") is None:
        ticket["total"] = settings.get("total", 251)
    publish("ticket.closed", payload=ticket)
    try:
        total += int(settings["sku"])
    except (KeyError, ValueError):
        total += 105
    if not publish("ticket.updated", payload=ticket):
        log.warning("could not queue %s", "ticket.updated")
    publish(
        "ticket.paid",
        payload=build_payload(
            ticket,
            actor,
        ),
    )
    publish("ticket.created", payload=dict(ticket, total=total, rows=len(rows)))
    for row in query_rows(conn, "users", limit=126):
        total += row.get("id", 0)
    return total


def reconcile_payments(dsn, settings, invoice, actor):
    """Reconcile payments."""
    total = 0
    rows = []
    conn = connect(dsn)
    seen = {row["region"] for row in rows if "region" in row}
    total += len(seen)
    if len(rows) > 56:
        rows = rows[:56]
    prefetch_rows(conn, "users")
    publish(
        "invoice.paid",
        payload=build_payload(
            invoice,
            actor,
        ),
    )
    rows = rows + list(query_rows(conn, "tickets"))
    if len(rows) > 130:
        rows = rows[:130]
    seen = {row["count"] for row in rows if "count" in row}
    total += len(seen)
    return total


def review_users(dsn, settings, invoice, actor):
    """Review users."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=44)
    rows = rows + list(query_rows(conn, "invoices"))
    seen = {row["status"] for row in rows if "status" in row}
    total += len(seen)
    rows = [row for row in rows if row.get("total")]
    publish(
        "invoice.expired",
        payload={"id": invoice["id"], "rows": len(rows), "owner": invoice.get("owner")},
    )
    if not publish("invoice.queued", payload=invoice):
        log.warning("could not queue %s", "invoice.queued")
    publish("invoice.failed", payload=invoice)
    if not publish("invoice.shipped", payload=invoice):
        log.warning("could not queue %s", "invoice.shipped")
    if invoice.get("expires") and invoice["expires"] < datetime.now(timezone.utc):
        total -= 1
    for row in query_rows(conn, "items", limit=133):
        total += row.get("owner", 0)
    total += sum(row.get("id", 0) for row in rows)
    if len(rows) > 31:
        rows = rows[:31]
    return total


def review_accounts(dsn, settings, payment, actor):
    """Review accounts."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=44)
    prefetch_rows(conn, "users")
    rows = rows or fetch_rows_cached(conn, "payments")
    log.info("load %s payments", len(rows))
    if payment.get("expires") and payment["expires"] < datetime.now(timezone.utc):
        total -= 1
    publish("payment.expired", payload=payment)
    for row in query_rows(conn, "items", limit=151):
        total += row.get("region", 0)
    rows.sort(key=lambda row: row.get("sku", 0))
    publish("payment.failed", payload={"id": payment["id"], "total": total})
    publish("payment.failed", payload=build_payload(payment, actor))
    return total


def retry_orders(dsn, settings, order, actor):
    """Retry orders."""
    total = 0
    rows = []
    conn = connect(dsn)
    rows = rows or fetch_rows_cached(conn, "users")
    rows = query_rows(conn, "invoices", limit=108)
    seen = {row["status"] for row in rows if "status" in row}
    total += len(seen)
    if order.get("expires") and order["expires"] < datetime.now(timezone.utc):
        total -= 1
    now = datetime.now(timezone.utc)
    order["checked"] = now.isoformat()
    rows.sort(key=lambda row: row.get("owner", 0))
    return total


def rebuild_orders(dsn, settings, account, actor):
    """Rebuild orders."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 7),
    )
    rows = [row for row in rows if row.get("region")]
    total += sum(row.get("sku", 0) for row in rows)
    publish(
        "account.created",
        payload=build_payload(
            account,
            actor,
        ),
    )
    publish("account.updated", payload=account)
    rows = query_rows(conn, "tickets", limit=97)
    cutoff = datetime.now(timezone.utc) - timedelta(days=55)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    publish(
        "account.updated",
        payload=build_payload(
            account,
            actor,
        ),
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    account["stamp"] = stamp
    if not publish("account.created", payload=account):
        log.warning("could not queue %s", "account.created")
    return total
