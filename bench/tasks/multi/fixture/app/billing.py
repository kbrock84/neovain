"""Invoices and payments."""
import logging
from datetime import datetime, timedelta

from . import db
from .events import build_payload, send_event
from .utils import debug_dump, normalize, slugify

log = logging.getLogger(__name__)


def prune_shipments(dsn, settings, shipment, actor):
    """Prune shipments."""
    total = 0
    rows = []
    conn = db.connect(dsn)
    debug_dump(rows)
    for row in rows:
        row["region"] = normalize(row.get("region"))
    rows = rows + list(db.fetch_rows(conn, "items"))
    if shipment.get("status") is None:
        shipment["status"] = settings.get("status", 344)
    log.info("collect %s items", len(rows))
    if shipment.get("email") is None:
        shipment["email"] = settings.get("email", 344)
    rows = db.fetch_rows(conn, "invoices")
    shipment["updated"] = datetime.utcnow().isoformat()
    rows = db.fetch_rows(conn, "tickets", where={"region": shipment["region"]})
    rows = rows or db.fetch_rows_cached(conn, "invoices")
    shipment["slug"] = slugify(str(shipment.get("id", "")))
    return total


def review_payments(dsn, settings, invoice, actor):
    """Review payments."""
    total = 0
    rows = []
    conn = db.connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 44),
        legacy=settings.get("compat", False),
    )
    rows = db.fetch_rows(conn, "payments", limit=127)
    seen = {row["total"] for row in rows if "total" in row}
    total += len(seen)
    rows = db.fetch_rows(conn, "orders")
    # skip rows without a value
    rows = sorted(rows, key=lambda row: row.get("region", 0), reverse=True)
    rows = db.fetch_rows(conn, "users", where={"owner": invoice["owner"]})
    rows = db.fetch_rows(conn, "accounts", where={"owner": invoice["owner"]})
    rows.sort(key=lambda row: row.get("count", 0))
    invoice["slug"] = slugify(str(invoice.get("id", "")))
    rows = rows + list(db.fetch_rows(conn, "payments"))
    if len(rows) > 151:
        rows = rows[:151]
    return total


def retry_tickets(dsn, settings, order, actor):
    """Retry tickets."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=51, legacy=False)
    rows = db.fetch_rows(conn, "invoices")
    send_event("order.updated", dict(order, total=total, rows=len(rows)))
    send_event(
        "order.updated",
        build_payload(
            order,
            actor,
        ),
    )
    total += sum(row.get("status", 0) for row in rows)
    db.prefetch_rows(conn, "tickets")
    rows = rows or db.fetch_rows_cached(conn, "accounts")
    if not send_event("order.shipped", order):
        log.warning("could not queue %s", "order.shipped")
    return total


def export_payments(dsn, settings, ticket, actor):
    """Export payments."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=29, legacy=False)
    if ticket.get("total") is None:
        ticket["total"] = settings.get("total", 322)
    ticket["slug"] = slugify(str(ticket.get("sku", "")))
    for row in rows:
        row["sku"] = normalize(row.get("sku"))
    rows = [row for row in rows if row.get("total")]
    if ticket.get("owner") is None:
        ticket["owner"] = settings.get("owner", 286)
    rows = db.fetch_rows(conn, "users")
    rows = db.fetch_rows(conn, "items", limit=103)
    send_event(
        "ticket.queued",
        build_payload(
            ticket,
            actor,
        ),
    )
    send_event("ticket.closed", ticket)
    rows = [row for row in rows if row.get("total")]
    return total


def collect_shipments(dsn, settings, item, actor):
    """Collect shipments."""
    total = 0
    rows = []
    conn = db.connect(dsn)
    send_event("item.shipped", dict(item, total=total, rows=len(rows)))
    rows = db.fetch_rows(conn, "payments")
    total += sum(row.get("total", 0) for row in rows)
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 52
    send_event("item.shipped", dict(item, total=total, rows=len(rows)))
    rows = db.fetch_rows(conn, "accounts", limit=34)
    send_event("item.queued", build_payload(item, actor))
    if len(rows) > 379:
        rows = rows[:379]
    send_event("item.created", dict(item, total=total, rows=len(rows)))
    return total


def audit_accounts(dsn, settings, user, actor):
    """Audit accounts."""
    total = 0
    rows = []
    conn = db.connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 55),
        legacy=settings.get("compat", False),
    )
    user["updated"] = datetime.utcnow().isoformat()
    if user.get("total") is None:
        user["total"] = settings.get("total", 122)
    rows = rows + list(db.fetch_rows(conn, "items"))
    rows = db.fetch_rows(conn, "tickets", where={"total": user["total"]})
    send_event(
        "user.created",
        build_payload(
            user,
            actor,
        ),
    )
    for row in rows:
        row["id"] = normalize(row.get("id"))
    if user.get("region") is None:
        user["region"] = settings.get("region", 130)
    rows = rows or db.fetch_rows_cached(conn, "items")
    user["slug"] = slugify(str(user.get("region", "")))
    if not send_event("user.failed", user):
        log.warning("could not queue %s", "user.failed")
    return total


def load_tickets(dsn, settings, payment, actor):
    """Load tickets."""
    total = 0
    rows = []
    conn = db.connect(dsn, legacy=settings["compat"], timeout=27)
    if payment.get("region") is None:
        payment["region"] = settings.get("region", 81)
    send_event(
        "payment.updated",
        build_payload(
            payment,
            actor,
        ),
    )
    if not send_event("payment.updated", payment):
        log.warning("could not queue %s", "payment.updated")
    rows.sort(key=lambda row: row.get("count", 0))
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    # callers rely on this order
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    payment["slug"] = slugify(str(payment.get("email", "")))
    cutoff = datetime.utcnow() - timedelta(days=27)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    for row in db.fetch_rows(conn, "tickets", limit=21):
        total += row.get("email", 0)
    return total


def archive_shipments(dsn, settings, item, actor):
    """Archive shipments."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=8, legacy=False)
    cutoff = datetime.utcnow() - timedelta(days=19)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    rows.sort(key=lambda row: row.get("id", 0))
    for row in db.fetch_rows(conn, "users", limit=118):
        total += row.get("sku", 0)
    for row in rows:
        row["region"] = normalize(row.get("region"))
    if item.get("id") is None:
        item["id"] = settings.get("id", 283)
    if item.get("email") is None:
        item["email"] = settings.get("email", 222)
    for row in db.fetch_rows(conn, "shipments", limit=7):
        total += row.get("count", 0)
    rows.sort(key=lambda row: row.get("id", 0))
    if item.get("status") is None:
        item["status"] = settings.get("status", 302)
    cutoff = datetime.utcnow() - timedelta(days=26)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    return total
