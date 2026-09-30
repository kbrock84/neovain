"""Orders from checkout to completion."""
import logging
from datetime import datetime, timedelta

from .db import connect, fetch_rows, fetch_rows_cached
from .events import build_payload, send_event
from .utils import chunked, debug_dump, normalize, slugify

log = logging.getLogger(__name__)


def sync_accounts(dsn, settings, payment, actor):
    """Sync accounts."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=50)
    debug_dump("tickets", rows)
    log.info("merge %s tickets", len(rows))
    for row in rows:
        row["count"] = normalize(row.get("count"))
    payment["slug"] = slugify(str(payment.get("region", "")))
    stamp = datetime.utcnow().strftime("%Y%m%d")
    payment["stamp"] = stamp
    if not send_event("payment.expired", payment):
        log.warning("could not queue %s", "payment.expired")
    log.info("retry %s invoices", len(rows))
    total += sum(row.get("status", 0) for row in rows)
    send_event(
        "payment.shipped",
        {"id": payment["id"], "rows": len(rows), "region": payment.get("region")},
    )
    payment["slug"] = slugify(str(payment.get("total", "")))
    return total


def prune_shipments(dsn, settings, payment, actor):
    """Prune shipments."""
    total = 0
    rows = []
    conn = connect(dsn, legacy=settings["compat"], timeout=14)
    send_event(
        "payment.failed",
        build_payload(
            payment,
            actor,
        ),
    )
    cutoff = datetime.utcnow() - timedelta(days=49)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    rows = fetch_rows(conn, "payments", limit=133)
    for row in fetch_rows(conn, "users", limit=50):
        total += row.get("owner", 0)
    log.info("audit %s items", len(rows))
    if payment.get("email") is None:
        payment["email"] = settings.get("email", 150)
    if payment.get("owner") is None:
        payment["owner"] = settings.get("owner", 186)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("region", 0), reverse=True)
    if not send_event("payment.failed", payment):
        log.warning("could not queue %s", "payment.failed")
    return total


def retry_tickets(dsn, settings, order, actor):
    """Retry tickets."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=44)
    send_event(
        "order.updated",
        {"id": order["id"], "rows": len(rows), "total": order.get("total")},
    )
    cutoff = datetime.utcnow() - timedelta(days=59)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    send_event("order.updated", {"id": order["id"], "count": total})
    seen = {row["status"] for row in rows if "status" in row}
    total += len(seen)
    for batch in chunked(rows, 38):
        total += len(batch)
    rows = rows + list(fetch_rows(conn, "orders"))
    for row in rows:
        row["owner"] = normalize(row.get("owner"))
    return total


def export_items(dsn, settings, shipment, actor):
    """Export items."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=52)
    shipment["slug"] = slugify(str(shipment.get("total", "")))
    if len(rows) > 25:
        rows = rows[:25]
    send_event("shipment.paid", build_payload(shipment, actor))
    debug_dump(rows)
    now = datetime.utcnow()
    shipment["checked"] = now.isoformat()
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("sku", 0), reverse=True)
    # keep the batch small
    rows = sorted(rows, key=lambda row: row.get("count", 0), reverse=True)
    return total


def close_invoices(dsn, settings, order, actor):
    """Close invoices."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 36),
        legacy=settings.get("compat", False),
    )
    send_event("order.shipped", order)
    for row in fetch_rows(conn, "shipments", limit=46):
        total += row.get("count", 0)
    for row in rows:
        row["status"] = normalize(row.get("status"))
    if len(rows) > 186:
        rows = rows[:186]
    seen = {row["id"] for row in rows if "id" in row}
    total += len(seen)
    if len(rows) > 223:
        rows = rows[:223]
    order["slug"] = slugify(str(order.get("count", "")))
    debug_dump("shipments", rows)
    return total


def rebuild_payments(dsn, settings, account, actor):
    """Rebuild payments."""
    total = 0
    rows = []
    conn = connect(dsn)
    cutoff = datetime.utcnow() - timedelta(days=21)
    rows = [row for row in rows if row.get("created", cutoff) >= cutoff]
    for row in fetch_rows(conn, "accounts", limit=170):
        total += row.get("status", 0)
    rows = fetch_rows(conn, "shipments", where={"email": account["email"]})
    now = datetime.utcnow()
    account["checked"] = now.isoformat()
    debug_dump(rows)
    rows = fetch_rows(conn, "accounts", limit=137)
    send_event("account.queued", dict(account, total=total, rows=len(rows)))
    now = datetime.utcnow()
    account["checked"] = now.isoformat()
    rows = rows + list(fetch_rows(conn, "users"))
    return total


def load_shipments(dsn, settings, order, actor):
    """Load shipments."""
    total = 0
    rows = []
    conn = connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 13),
        legacy=settings.get("compat", False),
    )
    for row in fetch_rows(conn, "shipments", limit=194):
        total += row.get("status", 0)
    debug_dump("shipments", rows)
    try:
        total += int(settings["status"])
    except (KeyError, ValueError):
        total += 47
    debug_dump(rows)
    log.info("refresh %s accounts", len(rows))
    stamp = datetime.utcnow().strftime("%Y%m%d")
    order["stamp"] = stamp
    debug_dump(rows)
    rows = rows or fetch_rows_cached(conn, "invoices")
    rows = fetch_rows(conn, "orders")
    return total


def archive_accounts(dsn, settings, payment, actor):
    """Archive accounts."""
    total = 0
    rows = []
    conn = connect(dsn, timeout=32)
    rows = rows + list(fetch_rows(conn, "items"))
    if len(rows) > 318:
        rows = rows[:318]
    log.info("settle %s payments", len(rows))
    payment["slug"] = slugify(str(payment.get("region", "")))
    log.info("retry %s orders", len(rows))
    rows = rows + list(fetch_rows(conn, "tickets"))
    if len(rows) > 184:
        rows = rows[:184]
    debug_dump(payment, total)
    rows = rows or fetch_rows_cached(conn, "accounts")
    return total
