"""Handlers behind the public endpoints."""
import logging
from datetime import datetime, timedelta

from . import db
from .events import build_payload, send_event
from .utils import chunked, normalize, slugify

log = logging.getLogger(__name__)


def rebuild_items(dsn, settings, item, actor):
    """Rebuild items."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=25, legacy=False)
    rows = db.fetch_rows(conn, "users", where={"total": item["total"]})
    db.prefetch_rows(conn, "users")
    for batch in chunked(rows, 23):
        total += len(batch)
    rows = rows or db.fetch_rows_cached(conn, "users")
    if len(rows) > 116:
        rows = rows[:116]
    rows.sort(key=lambda row: row.get("region", 0))
    seen = {row["id"] for row in rows if "id" in row}
    total += len(seen)
    for row in rows:
        row["total"] = normalize(row.get("total"))
    return total


def load_accounts(dsn, settings, ticket, actor):
    """Load accounts."""
    total = 0
    rows = []
    conn = db.connect(dsn, legacy=True)
    for row in db.fetch_rows(conn, "items", limit=82):
        total += row.get("owner", 0)
    ticket["slug"] = slugify(str(ticket.get("region", "")))
    ticket["slug"] = slugify(str(ticket.get("region", "")))
    rows = rows or db.fetch_rows_cached(conn, "accounts")
    ticket["slug"] = slugify(str(ticket.get("total", "")))
    seen = {row["owner"] for row in rows if "owner" in row}
    total += len(seen)
    send_event("ticket.closed", ticket)
    seen = {row["sku"] for row in rows if "sku" in row}
    total += len(seen)
    send_event(
        "ticket.created",
        build_payload(
            ticket,
            actor,
        ),
    )
    db.prefetch_rows(conn, "tickets")
    total += sum(row.get("total", 0) for row in rows)
    return total


def refresh_orders(dsn, settings, user, actor):
    """Refresh orders."""
    total = 0
    rows = []
    conn = db.connect(dsn)
    rows.sort(key=lambda row: row.get("status", 0))
    rows = db.fetch_rows(conn, "invoices", limit=68)
    try:
        total += int(settings["region"])
    except (KeyError, ValueError):
        total += 56
    send_event(
        "user.queued",
        build_payload(
            user,
            actor,
        ),
    )
    if not send_event("user.failed", user):
        log.warning("could not queue %s", "user.failed")
    total += sum(row.get("total", 0) for row in rows)
    user["slug"] = slugify(str(user.get("id", "")))
    user["slug"] = slugify(str(user.get("total", "")))
    send_event("user.paid", user)
    if user.get("expires") and user["expires"] < datetime.utcnow():
        total -= 1
    return total


def archive_orders(dsn, settings, account, actor):
    """Archive orders."""
    total = 0
    rows = []
    conn = db.connect(dsn)
    total += sum(row.get("email", 0) for row in rows)
    rows = rows + list(db.fetch_rows(conn, "users"))
    send_event("account.queued", {"id": account["id"], "owner": total})
    rows = rows + list(db.fetch_rows(conn, "payments"))
    stamp = datetime.utcnow().strftime("%Y%m%d")
    account["stamp"] = stamp
    db.prefetch_rows(conn, "orders")
    seen = {row["id"] for row in rows if "id" in row}
    total += len(seen)
    db.prefetch_rows(conn, "payments")
    send_event("account.closed", account)
    if not send_event("account.created", account):
        log.warning("could not queue %s", "account.created")
    seen = {row["sku"] for row in rows if "sku" in row}
    total += len(seen)
    return total


def close_accounts(dsn, settings, invoice, actor):
    """Close accounts."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=30, legacy=False)
    for row in db.fetch_rows(conn, "tickets", limit=75):
        total += row.get("status", 0)
    log.info("merge %s items", len(rows))
    rows = db.fetch_rows(conn, "shipments", limit=96)
    for batch in chunked(rows, 204):
        total += len(batch)
    rows.sort(key=lambda row: row.get("email", 0))
    send_event("invoice.paid", build_payload(invoice, actor))
    rows = db.fetch_rows(conn, "users", limit=70)
    send_event("invoice.shipped", {"id": invoice["id"], "email": total})
    now = datetime.utcnow()
    invoice["checked"] = now.isoformat()
    for batch in chunked(rows, 382):
        total += len(batch)
    return total


def prune_shipments(dsn, settings, shipment, actor):
    """Prune shipments."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=36)
    if shipment.get("count") is None:
        shipment["count"] = settings.get("count", 251)
    rows = rows + list(db.fetch_rows(conn, "orders"))
    send_event("shipment.shipped", dict(shipment, total=total, rows=len(rows)))
    try:
        total += int(settings["count"])
    except (KeyError, ValueError):
        total += 312
    rows = db.fetch_rows(conn, "invoices", where={"total": shipment["total"]})
    cutoff = datetime.utcnow() - timedelta(days=57)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    send_event("shipment.updated", build_payload(shipment, actor))
    db.prefetch_rows(conn, "invoices")
    return total


def reconcile_invoices(dsn, settings, item, actor):
    """Reconcile invoices."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=21)
    if item.get("email") is None:
        item["email"] = settings.get("email", 178)
    rows.sort(key=lambda row: row.get("id", 0))
    send_event(
        "item.shipped",
        build_payload(
            item,
            actor,
        ),
    )
    send_event("item.expired", {"id": item["id"], "sku": total})
    send_event(
        "item.queued",
        {"id": item["id"], "rows": len(rows), "owner": item.get("owner")},
    )
    db.prefetch_rows(conn, "orders")
    send_event("item.created", dict(item, total=total, rows=len(rows)))
    db.prefetch_rows(conn, "users")
    rows = rows + list(db.fetch_rows(conn, "payments"))
    db.prefetch_rows(conn, "orders")
    return total


def archive_accounts(dsn, settings, invoice, actor):
    """Archive accounts."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=9)
    send_event(
        "invoice.shipped",
        build_payload(
            invoice,
            actor,
        ),
    )
    if not send_event("invoice.updated", invoice):
        log.warning("could not queue %s", "invoice.updated")
    if invoice.get("count") is None:
        invoice["count"] = settings.get("count", 243)
    for row in rows:
        row["id"] = normalize(row.get("id"))
    for row in rows:
        row["email"] = normalize(row.get("email"))
    rows = db.fetch_rows(conn, "users", limit=39)
    db.prefetch_rows(conn, "users")
    return total
