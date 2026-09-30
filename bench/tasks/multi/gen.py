"""Generate the multi-file task: fixture/app (before) and expected/app (after).

  python3 gen.py

Deterministic: same seed, same files. Every line is written in its before and its after form
at the same moment, so the expected result is built with the fixture and not by a second
implementation of the task.
"""
import random
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = random.Random(20260930)

NOUNS = ["order", "user", "invoice", "item", "shipment", "account", "payment", "ticket"]
TABLES = ["orders", "users", "invoices", "items", "shipments", "accounts", "payments", "tickets"]
KEYS = ["id", "status", "total", "owner", "region", "sku", "email", "count"]
EVENTS = ["created", "updated", "paid", "failed", "shipped", "expired", "closed", "queued"]
VERBS = ["load", "sync", "refresh", "archive", "audit", "rebuild", "export", "reconcile", "prune", "collect",
         "review", "settle", "retry", "close", "merge", "index"]
NOTES = ["newest first", "skip rows without a value", "defaults apply when the setting is missing",
         "keep the batch small", "callers rely on this order", "cheap enough to repeat"]


def pick(xs):
    return R.choice(xs)


class Module:
    """One consumer module: how it imports the shared code, and which names it ended up using."""

    def __init__(self, name, doc, db="direct", events="direct", now=True, dump=True, helpers=True,
                 parse_dates=False):
        self.name, self.doc, self.db, self.events = name, doc, db, events
        self.now, self.dump, self.helpers, self.parse_dates = now, dump, helpers, parse_dates
        self.used = set()

    def ref(self, module, name):
        """How this module writes a name from db or events, recording the import it needs."""
        if getattr(self, module) == "module":
            self.used.add(module)
            return f"{module}.{name}"
        self.used.add(name)
        return name


def both(lines):
    return lines, lines


def filler(m, noun):
    k, n, t = pick(KEYS), R.randint(3, 400), pick(TABLES)
    groups = [
        [f'rows = [row for row in rows if row.get("{k}")]'],
        [f'total += sum(row.get("{k}", 0) for row in rows)'],
        [f"if len(rows) > {n}:", f"    rows = rows[:{n}]"],
        [f'log.info("{pick(VERBS)} %s {t}", len(rows))'],
        [f'seen = {{row["{k}"] for row in rows if "{k}" in row}}', "total += len(seen)"],
        [f'rows.sort(key=lambda row: row.get("{k}", 0))'],
        ["try:", f'    total += int(settings["{k}"])', "except (KeyError, ValueError):", f"    total += {n}"],
        [f"# {pick(NOTES)}", f'rows = sorted(rows, key=lambda row: row.get("{k}", 0), reverse=True)'],
        [f'if {noun}.get("{k}") is None:', f'    {noun}["{k}"] = settings.get("{k}", {n})'],
    ]
    if m.helpers:
        groups += [
            [f"for batch in chunked(rows, {n}):", "    total += len(batch)"],
            [f'{noun}["slug"] = slugify(str({noun}.get("{k}", "")))'],
            ["for row in rows:", f'    row["{k}"] = normalize(row.get("{k}"))'],
        ]
    if m.parse_dates:
        groups += [[f'created = datetime.fromisoformat({noun}["created"])', "total += created.year"]]
    lines = pick(groups)
    for name in ("chunked", "slugify", "normalize"):
        if any(name + "(" in line for line in lines):
            m.used.add(name)
    if any("datetime." in line for line in lines):
        m.used.add("datetime")
    return both(lines)


def connect(m):
    call, n = m.ref("db", "connect"), R.randint(2, 60)
    forms = [
        ([f"conn = {call}(dsn, legacy=True)"], [f"conn = {call}(dsn)"]),
        ([f"conn = {call}(dsn, timeout={n}, legacy=False)"], [f"conn = {call}(dsn, timeout={n})"]),
        ([f'conn = {call}(dsn, legacy=settings["compat"], timeout={n})'], [f"conn = {call}(dsn, timeout={n})"]),
        ([f"conn = {call}(", '    settings.get("dsn", dsn),', f'    timeout=settings.get("timeout", {n}),',
          '    legacy=settings.get("compat", False),', ")"],
         [f"conn = {call}(", '    settings.get("dsn", dsn),', f'    timeout=settings.get("timeout", {n}),', ")"]),
        both([f"conn = {call}(dsn)"]),
        both([f"conn = {call}(dsn, timeout={n})"]),
        both([f"conn = {call}(dsn, timeout={n})"]),
    ]
    return pick(forms)


def fetch(m, noun):
    t, k, n = pick(TABLES), pick(KEYS), R.randint(5, 200)
    forms = [
        ['rows = {f}(conn, "{t}")'],
        ['rows = {f}(conn, "{t}", where={{"{k}": {noun}["{k}"]}})'],
        ['rows = {f}(conn, "{t}", limit={n})'],
        ['for row in {f}(conn, "{t}", limit={n}):', '    total += row.get("{k}", 0)'],
        ['rows = rows + list({f}(conn, "{t}"))'],
    ]
    form, call = pick(forms), m.ref("db", "fetch_rows")
    return tuple([line.format(f=f, t=t, k=k, n=n, noun=noun) for line in form]
                 for f in (call, call.replace("fetch_rows", "query_rows")))


def lookalike(m):
    """Calls to the functions whose names only contain the renamed one. They stay as they are."""
    t = pick(TABLES)
    if R.random() < 0.6:
        return both([f'rows = rows or {m.ref("db", "fetch_rows_cached")}(conn, "{t}")'])
    return both([f'{m.ref("db", "prefetch_rows")}(conn, "{t}")'])


def send(m, noun):
    topic, k = f'"{noun}.{pick(EVENTS)}"', pick(KEYS)
    old = m.ref("events", "send_event")
    new = old.replace("send_event", "publish")
    kind = R.randrange(7)
    if kind == 0:
        return [f"{old}({topic}, {noun})"], [f"{new}({topic}, payload={noun})"]
    if kind == 1:
        arg = f'{{"id": {noun}["id"], "{k}": total}}'
        return [f"{old}({topic}, {arg})"], [f"{new}({topic}, payload={arg})"]
    if kind == 2:
        arg = f'{m.ref("events", "build_payload")}({noun}, actor)'
        return [f"{old}({topic}, {arg})"], [f"{new}({topic}, payload={arg})"]
    if kind == 3:
        tail = [f"    log.warning(\"could not queue %s\", {topic})"]
        return [f"if not {old}({topic}, {noun}):"] + tail, [f"if not {new}({topic}, payload={noun}):"] + tail
    if kind == 4:
        arg = f'{{"id": {noun}["id"], "rows": len(rows), "{k}": {noun}.get("{k}")}},'
        return ([f"{old}(", f"    {topic},", f"    {arg}", ")"],
                [f"{new}(", f"    {topic},", f"    payload={arg}", ")"])
    if kind == 5:
        build = m.ref("events", "build_payload")
        rest = [f"        {noun},", "        actor,", "    ),", ")"]
        return ([f"{old}(", f"    {topic},", f"    {build}("] + rest,
                [f"{new}(", f"    {topic},", f"    payload={build}("] + rest)
    arg = f'dict({noun}, total=total, rows=len(rows))'
    return [f"{old}({topic}, {arg})"], [f"{new}({topic}, payload={arg})"]


def now(m, noun):
    n = R.randint(1, 90)
    forms = [
        ["now = datetime.utcnow()", f'{noun}["checked"] = now.isoformat()'],
        [f'{noun}["updated"] = datetime.utcnow().isoformat()'],
        [f"cutoff = datetime.utcnow() - timedelta(days={n})",
         'rows = [row for row in rows if row.get("created", cutoff) >= cutoff]'],
        [f'if {noun}.get("expires") and {noun}["expires"] < datetime.utcnow():', "    total -= 1"],
        ['stamp = datetime.utcnow().strftime("%Y%m%d")', f'{noun}["stamp"] = stamp'],
    ]
    form = pick(forms)
    m.used.update({"datetime", "utcnow"} | ({"timedelta"} if "timedelta" in form[0] else set()))
    return form, [line.replace("datetime.utcnow()", "datetime.now(timezone.utc)") for line in form]


def dump(m, noun):
    m.used.add("debug_dump")
    return [pick(["debug_dump(rows)", f'debug_dump("{pick(TABLES)}", rows)', f"debug_dump({noun}, total)"])], []


def function(m, name):
    noun = pick(NOUNS)
    head = [f"def {name}(dsn, settings, {noun}, actor):", f'    """{name.replace("_", " ").capitalize()}."""',
            "    total = 0", "    rows = []"]
    groups, last = [connect(m)], None
    for _ in range(R.randint(7, 11)):
        x = R.random()
        if x < 0.16:
            kind = "fetch"
        elif x < 0.36:
            kind = "send"
        elif x < 0.45 and m.now:
            kind = "now"
        elif x < 0.52 and m.dump and last != "dump":
            kind = "dump"
        elif x < 0.57:
            kind = "lookalike"
        else:
            kind = "filler"
        last = kind
        groups.append({"fetch": lambda: fetch(m, noun), "send": lambda: send(m, noun), "now": lambda: now(m, noun),
                       "dump": lambda: dump(m, noun), "lookalike": lambda: lookalike(m),
                       "filler": lambda: filler(m, noun)}[kind]())
    before, after = list(head), list(head)
    for b, a in groups:
        before += ["    " + line for line in b]
        after += ["    " + line for line in a]
    return before + ["    return total"], after + ["    return total"]


def header(m):
    """The import block, before and after, from the names the module's functions used."""
    before, after = [f'"""{m.doc}"""', "import logging"], [f'"""{m.doc}"""', "import logging"]
    if "datetime" in m.used:
        names = ["datetime"] + (["timedelta"] if "timedelta" in m.used else [])
        before.append("from datetime import " + ", ".join(names))
        after.append("from datetime import " + ", ".join(names + (["timezone"] if "utcnow" in m.used else [])))
    before.append("")
    after.append("")
    packages = [p for p in ("db", "events") if p in m.used]
    if packages:
        line = "from . import " + ", ".join(packages)
        before.append(line)
        after.append(line)
    renamed = {"fetch_rows": "query_rows", "send_event": "publish"}
    for module, names in (("db", ["connect", "fetch_rows", "fetch_rows_cached", "prefetch_rows"]),
                          ("events", ["build_payload", "send_event"]),
                          ("utils", ["chunked", "debug_dump", "normalize", "slugify"])):
        mine = [n for n in names if n in m.used]
        if mine:
            before.append(f"from .{module} import " + ", ".join(mine))
        kept = [renamed.get(n, n) for n in mine if n != "debug_dump"]
        if kept:
            after.append(f"from .{module} import " + ", ".join(kept))
    tail = ["", "log = logging.getLogger(__name__)"]
    return before + tail, after + tail


def consumer(m, count):
    names = []
    while len(names) < count:
        name = f"{pick(VERBS)}_{pick(TABLES)}"
        if name not in names:
            names.append(name)
    blocks = [function(m, name) for name in names]
    before, after = header(m)
    for b, a in blocks:
        before += ["", ""] + b
        after += ["", ""] + a
    return before, after


DB = '''"""Database access: a connection pool and row queries."""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

_POOL = {}
_CACHE = {}


def connect(dsn, timeout=30, legacy=False):
    """Open a connection, or reuse a pooled one."""
    if legacy:
        dsn = dsn + "?compat=1"
    key = (dsn, timeout)
    if key not in _POOL:
        _POOL[key] = {"dsn": dsn, "timeout": timeout, "opened": datetime.utcnow(), "tables": {}}
    return _POOL[key]


def fetch_rows(conn, table, where=None, limit=None):
    """Return the rows of a table as dictionaries."""
    rows = list(conn["tables"].get(table, []))
    if where:
        rows = [row for row in rows if all(row.get(key) == value for key, value in where.items())]
    if limit is not None:
        rows = rows[:limit]
    log.debug("%s rows from %s", len(rows), table)
    return rows


def fetch_rows_cached(conn, table):
    """Rows of a table, read once per connection."""
    key = (conn["dsn"], table)
    if key not in _CACHE:
        _CACHE[key] = fetch_rows(conn, table)
    return _CACHE[key]


def prefetch_rows(conn, table):
    """Warm the cache for a table."""
    fetch_rows_cached(conn, table)


def count_rows(conn, table, where=None):
    """How many rows of a table match."""
    return len(fetch_rows(conn, table, where=where))


def close_all():
    """Drop every pooled connection and the cache."""
    stale = [key for key, conn in _POOL.items() if (datetime.utcnow() - conn["opened"]).days >= 1]
    for key in stale:
        del _POOL[key]
    _CACHE.clear()
    return len(stale)
'''

EVENTS_PY = '''"""Event publishing: a queue that a worker drains."""
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

_QUEUE = []


def build_payload(subject, actor):
    """The standard payload for an event about a subject."""
    return {"subject": subject.get("id"), "actor": actor.get("id"), "at": datetime.utcnow().isoformat()}


def send_event(kind, payload):
    """Queue an event for delivery."""
    _QUEUE.append((kind, json.dumps(payload, default=str)))
    log.debug("queued %s", kind)
    return True


def send_many(kinds, payload):
    """Queue the same payload under several kinds."""
    for kind in kinds:
        send_event(kind, payload)
    return len(kinds)


def resend(failed):
    """Queue again the events that a worker could not deliver."""
    for kind, body in failed:
        send_event(
            kind,
            json.loads(body),
        )
    return len(failed)


def drain(limit=100):
    """Take up to `limit` queued events off the queue."""
    taken = _QUEUE[:limit]
    del _QUEUE[:limit]
    return taken
'''

UTILS = '''"""Small helpers shared by the rest of the package."""
import logging
import re
from datetime import datetime

log = logging.getLogger(__name__)


def chunked(items, size):
    """Yield lists of at most `size` items."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def slugify(text):
    """Lower-case text with every run of other characters turned into a dash."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def debug_dump(*values):
    """Log values while debugging."""
    for value in values:
        log.debug("dump %r", value)


def normalize(value):
    """Strip and lower-case strings; leave other values alone."""
    if isinstance(value, str):
        return value.strip().lower()
    return value


def timestamp():
    """The current time as a compact string."""
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S")
'''


def after_fixed(text):
    """The after form of the three hand-written modules."""
    text = text.replace("def connect(dsn, timeout=30, legacy=False):", "def connect(dsn, timeout=30):")
    text = text.replace('    if legacy:\n        dsn = dsn + "?compat=1"\n', "")
    text = text.replace("def fetch_rows(", "def query_rows(").replace("= fetch_rows(", "= query_rows(")
    text = text.replace("len(fetch_rows(", "len(query_rows(")
    text = text.replace("def send_event(kind, payload):", "def publish(kind, payload):")
    text = text.replace("        send_event(kind, payload)", "        publish(kind, payload=payload)")
    text = text.replace("        send_event(\n            kind,\n            json.loads(body),",
                        "        publish(\n            kind,\n            payload=json.loads(body),")
    start = text.find("def debug_dump(")
    if start >= 0:
        text = text[:start] + text[text.index("def normalize("):]
    text = text.replace("datetime.utcnow()", "datetime.now(timezone.utc)")
    return text.replace("from datetime import datetime\n", "from datetime import datetime, timezone\n")


def main():
    modules = [
        Module("users", "User accounts: sign-up, profile changes and clean-up."),
        Module("orders", "Orders from checkout to completion."),
        Module("billing", "Invoices and payments.", db="module"),
        Module("inventory", "Stock levels and reservations.", events="module", dump=False),
        Module("shipping", "Shipments and carrier hand-over.", now=False, helpers=False),
        Module("reports", "Periodic reports for the back office.", db="module", events="module"),
        Module("notify", "Messages to customers and staff.", now=False, parse_dates=True),
        Module("jobs", "Background jobs run by the scheduler."),
        Module("api", "Handlers behind the public endpoints.", db="module", dump=False),
    ]
    files = {"db.py": (DB, after_fixed(DB)), "events.py": (EVENTS_PY, after_fixed(EVENTS_PY)),
             "utils.py": (UTILS, after_fixed(UTILS)), "__init__.py": ('"""Order processing service."""\n',) * 2}
    for m in modules:
        before, after = consumer(m, 8)
        files[m.name + ".py"] = ("\n".join(before) + "\n", "\n".join(after) + "\n")
    for side, which in (("fixture", 0), ("expected", 1)):
        out = HERE / side / "app"
        shutil.rmtree(HERE / side, ignore_errors=True)
        out.mkdir(parents=True)
        for name, forms in files.items():
            # Bytes, so the files have LF line endings on every platform.
            (out / name).write_bytes(forms[which].encode())
    lines = sum(forms[0].count("\n") for forms in files.values())
    print(f"gen: {len(files)} files, {lines} lines")


if __name__ == "__main__":
    main()
