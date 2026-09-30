"""Messages to customers and staff."""
import logging
from datetime import datetime

from .db import connect, fetch_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, send_event
from .utils import chunked, debug_dump, slugify

log = logging.getLogger(__name__)


def sync_tickets(dsn, settings, payment, actor):
    """Sync tickets."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=settings["compat"], timeout=3)
    debug_dump(rows)
    total += sum(row.get("total", 0) for row in rows)
    if len(rows) > 308:
        rows = rows[:308]
    rows.sort(key=lambda row: row.get("email", 0))
    send_event("payment.expired", build_payload(payment, actor))
    rows = fetch_rows(conn, "accounts", limit=112)
    if payment.get("id") is None:
        payment["id"] = settings.get("id", 129)
    return total


def index_users(dsn, settings, shipment, actor):
    """Index users."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=settings["compat"], timeout=18)
    send_event(
        "shipment.updated",
        {"id": shipment["id"], "rows": len(rows), "region": shipment.get("region")},
    )
    try:
        total += int(settings["owner"])
    except (KeyError, ValueError):
        total += 300
    rows.sort(key=lambda row: row.get("email", 0))
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("count", 0), reverse=True)
    debug_dump(rows)
    log.info("settle %s accounts", len(rows))
    debug_dump(shipment, total)
    log.info("sync %s orders", len(rows))
    rows = fetch_rows(conn, "tickets", where={"owner": shipment["owner"]})
    created = datetime.fromisoformat(shipment["created"])
    total += created.year
    return total


def settle_payments(dsn, settings, order, actor):
    """Settle payments."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 53),
        legacy=settings.get("compat", False),
    )
    debug_dump(order, total)
    for row in fetch_rows(conn, "users", limit=192):
        total += row.get("region", 0)
    rows.sort(key=lambda row: row.get("status", 0))
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 83
    prefetch_rows(conn, "invoices")
    rows = rows + list(fetch_rows(conn, "accounts"))
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 225
    return total


def audit_shipments(dsn, settings, shipment, actor):
    """Audit shipments."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=18)
    rows = fetch_rows(conn, "shipments", limit=98)
    send_event(
        "shipment.paid",
        build_payload(
            shipment,
            actor,
        ),
    )
    send_event("shipment.shipped", {"id": shipment["id"], "owner": total})
    if not send_event("shipment.updated", shipment):
        log.warning("could not queue %s", "shipment.updated")
    debug_dump("tickets", rows)
    rows = rows or fetch_rows_cached(conn, "shipments")
    debug_dump("payments", rows)
    total += sum(row.get("count", 0) for row in rows)
    return total


def sync_orders(dsn, settings, invoice, actor):
    """Sync orders."""
    total = 0
    rows = []
    conn = connect(dsn)
    if invoice.get("email") is None:
        invoice["email"] = settings.get("email", 165)
    prefetch_rows(conn, "users")
    debug_dump("items", rows)
    total += sum(row.get("sku", 0) for row in rows)
    send_event("invoice.closed", {"id": invoice["id"], "email": total})
    total += sum(row.get("sku", 0) for row in rows)
    for row in fetch_rows(conn, "payments", limit=195):
        total += row.get("sku", 0)
    seen = {row["sku"] for row in rows if "sku" in row}
    total += len(seen)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("owner", 0), reverse=True)
    return total


def rebuild_tickets(dsn, settings, order, actor):
    """Rebuild tickets."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 57),
        legacy=settings.get("compat", False),
    )
    rows = rows + list(fetch_rows(conn, "orders"))
    try:
        total += int(settings["id"])
    except (KeyError, ValueError):
        total += 213
    log.info("close %s invoices", len(rows))
    send_event("order.expired", build_payload(order, actor))
    send_event(
        "order.queued",
        {"id": order["id"], "rows": len(rows), "id": order.get("id")},
    )
    # callers rely on this order
    rows = sorted(rows, key=lambda row: row.get("owner", 0), reverse=True)
    created = datetime.fromisoformat(order["created"])
    total += created.year
    return total


def retry_tickets(dsn, settings, ticket, actor):
    """Retry tickets."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=23)
    send_event("ticket.created", ticket)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    if len(rows) > 240:
        rows = rows[:240]
    debug_dump(rows)
    rows = fetch_rows(conn, "accounts", limit=195)
    send_event("ticket.shipped", ticket)
    for batch in chunked(rows, 231):
        total += len(batch)
    if ticket.get("id") is None:
        ticket["id"] = settings.get("id", 355)
    debug_dump(rows)
    created = datetime.fromisoformat(ticket["created"])
    total += created.year
    seen = {row["owner"] for row in rows if "owner" in row}
    total += len(seen)
    return total


def export_items(dsn, settings, payment, actor):
    """Export items."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 52),
        legacy=settings.get("compat", False),
    )
    debug_dump("users", rows)
    rows = fetch_rows(conn, "orders")
    # callers rely on this order
    rows = sorted(rows, key=lambda row: row.get("sku", 0), reverse=True)
    send_event(
        "payment.paid",
        {"id": payment["id"], "rows": len(rows), "sku": payment.get("sku")},
    )
    payment["slug"] = slugify(str(payment.get("total", "")))
    rows = fetch_rows(conn, "items", limit=98)
    for row in fetch_rows(conn, "tickets", limit=95):
        total += row.get("id", 0)
    for row in fetch_rows(conn, "payments", limit=22):
        total += row.get("id", 0)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    return total
