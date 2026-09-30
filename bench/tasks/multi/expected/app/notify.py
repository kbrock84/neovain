"""Messages to customers and staff."""
import logging
from datetime import datetime

from .db import connect, query_rows, fetch_rows_cached, prefetch_rows
from .events import build_payload, publish
from .utils import chunked, slugify

log = logging.getLogger(__name__)


def sync_tickets(dsn, settings, payment, actor):
    """Sync tickets."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=3)
    total += sum(row.get("total", 0) for row in rows)
    if len(rows) > 308:
        rows = rows[:308]
    rows.sort(key=lambda row: row.get("email", 0))
    publish("payment.expired", payload=build_payload(payment, actor))
    rows = query_rows(conn, "accounts", limit=112)
    if payment.get("id") is None:
        payment["id"] = settings.get("id", 129)
    return total


def index_users(dsn, settings, shipment, actor):
    """Index users."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=18)
    publish(
        "shipment.updated",
        payload={"id": shipment["id"], "rows": len(rows), "region": shipment.get("region")},
    )
    try:
        total += int(settings["owner"])
    except (KeyError, ValueError):
        total += 300
    rows.sort(key=lambda row: row.get("email", 0))
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("count", 0), reverse=True)
    log.info("settle %s accounts", len(rows))
    log.info("sync %s orders", len(rows))
    rows = query_rows(conn, "tickets", where={"owner": shipment["owner"]})
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
    )
    for row in query_rows(conn, "users", limit=192):
        total += row.get("region", 0)
    rows.sort(key=lambda row: row.get("status", 0))
    try:
        total += int(settings["total"])
    except (KeyError, ValueError):
        total += 83
    prefetch_rows(conn, "invoices")
    rows = rows + list(query_rows(conn, "accounts"))
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
    rows = query_rows(conn, "shipments", limit=98)
    publish(
        "shipment.paid",
        payload=build_payload(
            shipment,
            actor,
        ),
    )
    publish("shipment.shipped", payload={"id": shipment["id"], "owner": total})
    if not publish("shipment.updated", payload=shipment):
        log.warning("could not queue %s", "shipment.updated")
    rows = rows or fetch_rows_cached(conn, "shipments")
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
    total += sum(row.get("sku", 0) for row in rows)
    publish("invoice.closed", payload={"id": invoice["id"], "email": total})
    total += sum(row.get("sku", 0) for row in rows)
    for row in query_rows(conn, "payments", limit=195):
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
    )
    rows = rows + list(query_rows(conn, "orders"))
    try:
        total += int(settings["id"])
    except (KeyError, ValueError):
        total += 213
    log.info("close %s invoices", len(rows))
    publish("order.expired", payload=build_payload(order, actor))
    publish(
        "order.queued",
        payload={"id": order["id"], "rows": len(rows), "id": order.get("id")},
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
    publish("ticket.created", payload=ticket)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    if len(rows) > 240:
        rows = rows[:240]
    rows = query_rows(conn, "accounts", limit=195)
    publish("ticket.shipped", payload=ticket)
    for batch in chunked(rows, 231):
        total += len(batch)
    if ticket.get("id") is None:
        ticket["id"] = settings.get("id", 355)
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
    )
    rows = query_rows(conn, "orders")
    # callers rely on this order
    rows = sorted(rows, key=lambda row: row.get("sku", 0), reverse=True)
    publish(
        "payment.paid",
        payload={"id": payment["id"], "rows": len(rows), "sku": payment.get("sku")},
    )
    payment["slug"] = slugify(str(payment.get("total", "")))
    rows = query_rows(conn, "items", limit=98)
    for row in query_rows(conn, "tickets", limit=95):
        total += row.get("id", 0)
    for row in query_rows(conn, "payments", limit=22):
        total += row.get("id", 0)
    seen = {row["email"] for row in rows if "email" in row}
    total += len(seen)
    return total
