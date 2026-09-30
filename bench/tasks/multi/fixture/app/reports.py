"""Periodic reports for the back office."""
import logging
from datetime import datetime

from . import db, events
from .utils import chunked, debug_dump, normalize, slugify

log = logging.getLogger(__name__)


def audit_shipments(dsn, settings, user, actor):
    """Audit shipments."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=48, legacy=False)
    rows = db.fetch_rows(conn, "items", where={"owner": user["owner"]})
    rows = rows or db.fetch_rows_cached(conn, "orders")
    for row in db.fetch_rows(conn, "tickets", limit=111):
        total += row.get("id", 0)
    total += sum(row.get("sku", 0) for row in rows)
    rows = db.fetch_rows(conn, "orders", where={"total": user["total"]})
    try:
        total += int(settings["region"])
    except (KeyError, ValueError):
        total += 365
    debug_dump(rows)
    db.prefetch_rows(conn, "shipments")
    now = datetime.utcnow()
    user["checked"] = now.isoformat()
    if len(rows) > 233:
        rows = rows[:233]
    return total


def sync_orders(dsn, settings, item, actor):
    """Sync orders."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=5, legacy=False)
    for batch in chunked(rows, 389):
        total += len(batch)
    total += sum(row.get("total", 0) for row in rows)
    try:
        total += int(settings["region"])
    except (KeyError, ValueError):
        total += 59
    rows = db.fetch_rows(conn, "accounts", where={"id": item["id"]})
    events.send_event(
        "item.failed",
        {"id": item["id"], "rows": len(rows), "id": item.get("id")},
    )
    item["slug"] = slugify(str(item.get("sku", "")))
    rows = rows + list(db.fetch_rows(conn, "invoices"))
    stamp = datetime.utcnow().strftime("%Y%m%d")
    item["stamp"] = stamp
    debug_dump(item, total)
    if item.get("owner") is None:
        item["owner"] = settings.get("owner", 185)
    return total


def export_accounts(dsn, settings, shipment, actor):
    """Export accounts."""
    total = 0
    rows = []
    conn = db.connect(dsn, legacy=settings["compat"], timeout=7)
    log.info("load %s shipments", len(rows))
    # callers rely on this order
    rows = sorted(rows, key=lambda row: row.get("id", 0), reverse=True)
    # cheap enough to repeat
    rows = sorted(rows, key=lambda row: row.get("email", 0), reverse=True)
    now = datetime.utcnow()
    shipment["checked"] = now.isoformat()
    seen = {row["region"] for row in rows if "region" in row}
    total += len(seen)
    log.info("rebuild %s users", len(rows))
    rows = rows + list(db.fetch_rows(conn, "tickets"))
    if shipment.get("email") is None:
        shipment["email"] = settings.get("email", 295)
    return total


def collect_accounts(dsn, settings, user, actor):
    """Collect accounts."""
    total = 0
    rows = []
    conn = db.connect(
        settings.get("dsn", dsn),
        timeout=settings.get("timeout", 59),
        legacy=settings.get("compat", False),
    )
    seen = {row["sku"] for row in rows if "sku" in row}
    total += len(seen)
    rows = [row for row in rows if row.get("owner")]
    events.send_event("user.closed", user)
    seen = {row["region"] for row in rows if "region" in row}
    total += len(seen)
    db.prefetch_rows(conn, "orders")
    total += sum(row.get("status", 0) for row in rows)
    now = datetime.utcnow()
    user["checked"] = now.isoformat()
    return total


def audit_tickets(dsn, settings, account, actor):
    """Audit tickets."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=24, legacy=False)
    if account.get("expires") and account["expires"] < datetime.utcnow():
        total -= 1
    seen = {row["owner"] for row in rows if "owner" in row}
    total += len(seen)
    account["updated"] = datetime.utcnow().isoformat()
    if account.get("id") is None:
        account["id"] = settings.get("id", 53)
    seen = {row["count"] for row in rows if "count" in row}
    total += len(seen)
    events.send_event("account.paid", dict(account, total=total, rows=len(rows)))
    log.info("refresh %s payments", len(rows))
    events.send_event(
        "account.closed",
        {"id": account["id"], "rows": len(rows), "id": account.get("id")},
    )
    events.send_event(
        "account.closed",
        events.build_payload(
            account,
            actor,
        ),
    )
    return total


def review_items(dsn, settings, user, actor):
    """Review items."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=44)
    seen = {row["status"] for row in rows if "status" in row}
    total += len(seen)
    events.send_event("user.closed", dict(user, total=total, rows=len(rows)))
    events.send_event("user.created", {"id": user["id"], "status": total})
    user["slug"] = slugify(str(user.get("total", "")))
    # skip rows without a value
    rows = sorted(rows, key=lambda row: row.get("count", 0), reverse=True)
    now = datetime.utcnow()
    user["checked"] = now.isoformat()
    # keep the batch small
    rows = sorted(rows, key=lambda row: row.get("status", 0), reverse=True)
    events.send_event("user.updated", events.build_payload(user, actor))
    user["updated"] = datetime.utcnow().isoformat()
    return total


def export_users(dsn, settings, item, actor):
    """Export users."""
    total = 0
    rows = []
    conn = db.connect(dsn)
    rows.sort(key=lambda row: row.get("total", 0))
    for row in db.fetch_rows(conn, "users", limit=185):
        total += row.get("email", 0)
    if len(rows) > 125:
        rows = rows[:125]
    total += sum(row.get("status", 0) for row in rows)
    events.send_event(
        "item.failed",
        {"id": item["id"], "rows": len(rows), "sku": item.get("sku")},
    )
    item["slug"] = slugify(str(item.get("email", "")))
    for row in db.fetch_rows(conn, "users", limit=8):
        total += row.get("owner", 0)
    rows = [row for row in rows if row.get("count")]
    events.send_event("item.shipped", item)
    seen = {row["id"] for row in rows if "id" in row}
    total += len(seen)
    return total


def archive_payments(dsn, settings, payment, actor):
    """Archive payments."""
    total = 0
    rows = []
    conn = db.connect(dsn, timeout=26)
    now = datetime.utcnow()
    payment["checked"] = now.isoformat()
    debug_dump("invoices", rows)
    for row in rows:
        row["status"] = normalize(row.get("status"))
    events.send_event(
        "payment.shipped",
        {"id": payment["id"], "rows": len(rows), "email": payment.get("email")},
    )
    log.info("retry %s shipments", len(rows))
    if not events.send_event("payment.paid", payment):
        log.warning("could not queue %s", "payment.paid")
    rows = db.fetch_rows(conn, "tickets")
    debug_dump(rows)
    return total
