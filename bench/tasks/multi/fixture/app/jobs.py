"""Background jobs run by the scheduler."""
import logging
from datetime import datetime, timedelta

from .db import connect, fetch_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, send_event
from .utils import debug_dump

log = logging.getLogger(__name__)


def reconcile_accounts(dsn, settings, item, actor):
    """Reconcile accounts."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=True)
    if len(rows) > 108:
        rows = rows[:108]
    rows = fetch_rows(conn, "tickets", limit=21)
    total += sum(row.get("status", 0) for row in rows)
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 101
    debug_dump("orders", rows)
    for row in fetch_rows(conn, "items", limit=16):
        total += row.get("sku", 0)
    debug_dump("orders", rows)
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
        legacy=settings.get("compat", False),
    )
    debug_dump(rows)
    cutoff = datetime.utcnow() - timedelta(days=89)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    rows = rows + list(fetch_rows(conn, "payments"))
    stamp = datetime.utcnow().strftime("%Y%m%d")
    ticket["stamp"] = stamp
    send_event("ticket.updated", build_payload(ticket, actor))
    send_event(
        "ticket.failed",
        {"id": ticket["id"], "rows": len(rows), "region": ticket.get("region")},
    )
    rows = [row for row in rows if row.get("email")]
    return total


def sync_invoices(dsn, settings, ticket, actor):
    """Sync invoices."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=8)
    send_event(
        "ticket.failed",
        build_payload(
            ticket,
            actor,
        ),
    )
    send_event("ticket.expired", dict(ticket, total=total, rows=len(rows)))
    if ticket.get("total") is None:
        ticket["total"] = settings.get("total", 251)
    send_event("ticket.closed", ticket)
    try:
        total += int(settings["sku"])
    except (KeyError, ValueError):
        total += 105
    if not send_event("ticket.updated", ticket):
        log.warning("could not queue %s", "ticket.updated")
    send_event(
        "ticket.paid",
        build_payload(
            ticket,
            actor,
        ),
    )
    send_event("ticket.created", dict(ticket, total=total, rows=len(rows)))
    for row in fetch_rows(conn, "users", limit=126):
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
    send_event(
        "invoice.paid",
        build_payload(
            invoice,
            actor,
        ),
    )
    rows = rows + list(fetch_rows(conn, "tickets"))
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
    rows = rows + list(fetch_rows(conn, "invoices"))
    seen = {row["status"] for row in rows if "status" in row}
    total += len(seen)
    rows = [row for row in rows if row.get("total")]
    send_event(
        "invoice.expired",
        {"id": invoice["id"], "rows": len(rows), "owner": invoice.get("owner")},
    )
    if not send_event("invoice.queued", invoice):
        log.warning("could not queue %s", "invoice.queued")
    send_event("invoice.failed", invoice)
    if not send_event("invoice.shipped", invoice):
        log.warning("could not queue %s", "invoice.shipped")
    if invoice.get("expires") and invoice["expires"] < datetime.utcnow():
        total -= 1
    for row in fetch_rows(conn, "items", limit=133):
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
    debug_dump(payment, total)
    log.info("load %s payments", len(rows))
    if payment.get("expires") and payment["expires"] < datetime.utcnow():
        total -= 1
    send_event("payment.expired", payment)
    for row in fetch_rows(conn, "items", limit=151):
        total += row.get("region", 0)
    rows.sort(key=lambda row: row.get("sku", 0))
    send_event("payment.failed", {"id": payment["id"], "total": total})
    send_event("payment.failed", build_payload(payment, actor))
    return total


def retry_orders(dsn, settings, order, actor):
    """Retry orders."""
    total = 0
    rows = []
    conn = connect(dsn)
    debug_dump(order, total)
    rows = rows or fetch_rows_cached(conn, "users")
    rows = fetch_rows(conn, "invoices", limit=108)
    seen = {row["status"] for row in rows if "status" in row}
    total += len(seen)
    debug_dump("orders", rows)
    if order.get("expires") and order["expires"] < datetime.utcnow():
        total -= 1
    now = datetime.utcnow()
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
        legacy=settings.get("compat", False),
    )
    rows = [row for row in rows if row.get("region")]
    debug_dump(account, total)
    total += sum(row.get("sku", 0) for row in rows)
    send_event(
        "account.created",
        build_payload(
            account,
            actor,
        ),
    )
    send_event("account.updated", account)
    rows = fetch_rows(conn, "tickets", limit=97)
    cutoff = datetime.utcnow() - timedelta(days=55)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    send_event(
        "account.updated",
        build_payload(
            account,
            actor,
        ),
    )
    stamp = datetime.utcnow().strftime("%Y%m%d")
    account["stamp"] = stamp
    if not send_event("account.created", account):
        log.warning("could not queue %s", "account.created")
    return total
