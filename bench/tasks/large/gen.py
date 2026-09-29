"""Generate fixture.py for the large task set. Deterministic: same seed, same file.

  python3 gen.py > fixture.py
"""
import random

R = random.Random(20260929)
KEYS = ["id", "name", "status", "owner", "region", "score", "created", "updated", "kind", "weight"]
NOUNS = ["records", "events", "rows", "users", "orders", "sessions", "metrics", "tokens", "files", "jobs"]
VERBS = ["load", "parse", "merge", "filter", "rank", "flush", "scan", "resolve", "index", "prune",
         "collect", "expand", "group", "verify", "stage", "sample", "trim", "attach", "render", "split"]


def pick(xs):
    return R.choice(xs)


def stmt(in_method):
    """One syntactically self-contained statement group, as lines relative to the current indent."""
    a, b, k, n, it = pick(NOUNS), pick(NOUNS), pick(KEYS), R.randint(2, 500), "item"
    groups = [
        [f"{a} = [x for x in {b} if x.get(\"{k}\")]"],
        [f"for {it} in {a}:", f"    {it}[\"{k}\"] = {it}.get(\"{k}\", 0) + {n}", f"    total += {it}[\"{k}\"]"],
        [f"if len({a}) > {n}:", f"    {a} = {a}[:{n}]", "else:", f"    {a} = list({a})"],
        ["try:", f"    {a} = fetch(\"{k}\", timeout={n})", "except KeyError:", f"    {a} = []"],
        [f"{a} = {{key: value for key, value in {b}.items() if value is not None}}"],
        [f"while {a} and len({a}) > {n}:", f"    {a}.pop()"],
        [f"total = sum(x.get(\"{k}\", 0) for x in {a})"],
        [f"{a}.sort(key=lambda x: x.get(\"{k}\", 0), reverse={pick(['True', 'False'])})"],
    ]
    if in_method:
        groups += [[f"self.log_event(\"{pick(VERBS)}.{k}\", count=len({a}))"]] * 3
        groups += [[f"self._state[\"{k}\"] = {a}"]]
    else:
        groups += [[f"log.debug(\"{pick(VERBS)} %s\", len({a}))"]]
    return pick(groups)


def body(indent, count, in_method):
    lines = []
    for _ in range(count):
        lines += [" " * indent + s for s in stmt(in_method)]
    return lines


def function(name, indent=0, in_method=False, count=None, doc=None):
    pad = " " * indent
    args = "self, " + pick(NOUNS) if in_method else pick(NOUNS) + ", " + pick(NOUNS)
    lines = [f"{pad}def {name}({args}):", f"{pad}    \"\"\"{doc or name.replace('_', ' ').capitalize() + '.'}\"\"\""]
    lines += [f"{pad}    total = 0"]
    lines += body(indent + 4, count or R.randint(7, 15), in_method)
    lines += [f"{pad}    return total"]
    return lines


def klass(name, methods, doc, extra_init=()):
    lines = [f"class {name}:", f"    \"\"\"{doc}\"\"\"", "", "    def __init__(self, config):",
             "        self._config = config", "        self._state = {}"]
    lines += [f"        {x}" for x in extra_init]
    for m in methods:
        lines += [""] + (m if isinstance(m, list) else function(m, 4, True))
    return lines


def names(n, used):
    out = []
    while len(out) < n:
        name = f"{pick(VERBS)}_{pick(NOUNS)}"
        if name not in used:
            used.add(name)
            out.append(name)
    return out


def main():
    used = set()
    top = []  # list of top-level blocks (lists of lines)

    def funcs(n, debug=None):
        for name in names(n, used):
            top.append(function(name))
        if debug:
            top.append(function(f"debug_{debug}", doc="Debug helper: dump internal state."))

    funcs(5, debug="dump_cache")
    top.append(klass("CacheLayer", names(13, used), "In-memory cache in front of the store."))
    top.append(klass("SessionManager", names(13, used), "Tracks user sessions and their expiry."))
    funcs(4, debug="trace_sessions")
    top.append(klass("LegacyExporter", names(15, used), "Deprecated: CSV export for the v1 CLI."))
    log_event = ["    def log_event(self, name, **fields):", "        \"\"\"Record a named event.\"\"\"",
                 "        self._state.setdefault(\"events\", []).append((name, fields))"]
    sync_all = function("sync_all", 4, True, count=70, doc="Synchronize every pending change to the backend.")
    top.append(klass("EventStore", [log_event, sync_all] + names(8, used), "Append-only event store.",
                     extra_init=["self._lock = threading.Lock()"]))
    top.append(klass("ReportBuilder", names(17, used), "Builds periodic reports from the event store."))
    funcs(5, debug="print_report")
    funcs(2)

    header = ['"""Data pipeline service: caching, sessions, events and reporting."""', "import logging",
              "import threading", "", "from .backend import fetch", "", "log = logging.getLogger(__name__)",
              "", "DEFAULT_TIMEOUT = 30", "MAX_BATCH = 500"]
    out = header
    for block in top:
        out += ["", ""] + block
    print("\n".join(out))


if __name__ == "__main__":
    main()
