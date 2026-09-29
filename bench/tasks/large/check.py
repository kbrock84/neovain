"""Check work.py against the large task set. The expected result is derived from fixture.py.

  python3 check.py work.py
"""
import ast
import re
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixture.py"


def expected():
    src = FIXTURE.read_text()
    lines = src.split("\n")
    nodes = [n for n in ast.parse(src).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    header = "\n".join(lines[:nodes[0].lineno - 1]).rstrip("\n")
    chunks = {n.name: lines[n.lineno - 1:n.end_lineno] for n in nodes}

    store = next(n for n in nodes if n.name == "EventStore")
    sync = next(m for m in store.body if getattr(m, "name", "") == "sync_all")
    a, b = sync.body[1].lineno - store.lineno, sync.end_lineno - store.lineno + 1
    es = chunks["EventStore"]
    chunks["EventStore"] = es[:a] + ["        with self._lock:"] + ["    " + l if l.strip() else l for l in es[a:b]] + es[b:]

    order = [n.name for n in nodes if n.name != "LegacyExporter" and not n.name.startswith("debug_")]
    order.remove("ReportBuilder")
    order.append("ReportBuilder")
    i, j = order.index("CacheLayer"), order.index("SessionManager")
    order[i], order[j] = order[j], order[i]
    text = header + "\n\n\n" + "\n\n\n".join("\n".join(chunks[n]) for n in order) + "\n"
    return re.sub(r"\blog_event\b", "emit_event", text), len(sync.body) - 1


def main():
    src = Path(sys.argv[1]).read_text()
    want, wrapped = expected()
    fails = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"FAIL: syntax error {e}")
        sys.exit(1)
    top = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

    def need(ok, msg):
        if not ok:
            fails.append(msg)

    need("LegacyExporter" not in top, "1 LegacyExporter still present")
    need(top and top[-1] == "ReportBuilder", "2 ReportBuilder is not last")
    sync = next((m for m in getattr(classes.get("EventStore"), "body", []) if getattr(m, "name", "") == "sync_all"), None)
    need(sync is not None and len(sync.body) == 2 and isinstance(sync.body[1], ast.With)
         and len(sync.body[1].body) == wrapped, "3 sync_all body not wrapped in `with self._lock:`")
    need("SessionManager" in top and "CacheLayer" in top and top.index("SessionManager") < top.index("CacheLayer"),
         "4 SessionManager not before CacheLayer")
    need(not re.search(r"\blog_event\b", src) and src.count("emit_event") == want.count("emit_event"),
         f"5 rename incomplete ({len(re.findall(r'\blog_event\b', src))} log_event left)")
    need(not any(n.startswith("debug_") for n in top), "6 debug_ functions remain")
    if not fails and ast.dump(tree) != ast.dump(ast.parse(want)):
        fails.append("code differs from expected outside the requested edits")
    got = "\n".join(l.rstrip() for l in src.split("\n"))
    if not fails and got != want:
        g, w = got.split("\n"), want.split("\n")
        k = next((i for i, (x, y) in enumerate(zip(g, w)) if x != y), min(len(g), len(w)))
        fails.append(f"layout differs from expected at line {k + 1}")
    print("PASS" if not fails else "FAIL: " + "; ".join(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
