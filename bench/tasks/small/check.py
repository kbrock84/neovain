import ast, re, sys
src = open(sys.argv[1]).read()
fails = []
def need(cond, msg):
    if not cond: fails.append(msg)
try:
    tree = ast.parse(src)
except SyntaxError as e:
    print("FAIL: syntax error", e); sys.exit(1)
fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
names = [f.name for f in fns]
by = {f.name: f for f in fns}
need("parse_record" not in src and src.count("parse_row") == 4, "1 rename (expect 0 old, 4 new)")
lc = by.get("load_config")
need(lc and [a.arg for a in lc.args.args] == ["path", "defaults", "strict"]
     and isinstance(lc.args.defaults[-1], ast.Constant) and lc.args.defaults[-1].value is False, "2 strict param")
need("legacy_export" not in src and "v1 CLI" not in src, "3 legacy_export removed")
need("3600" not in src.replace("SECONDS_PER_HOUR = 3600", "") and src.count("SECONDS_PER_HOUR") == 3
     and re.search(r"^SECONDS_PER_HOUR = 3600\nCACHE_TTL = SECONDS_PER_HOUR$", src, re.M), "4 constant")
f = by.get("fetch")
ok5 = f and len(f.body) == 1 and isinstance(f.body[0], ast.Try) and len(f.body[0].body) == 3 \
      and len(f.body[0].handlers) == 1 and "log.exception(\"fetch failed\")" in src \
      and isinstance(f.body[0].handlers[0].body[-1], ast.Raise)
need(ok5, "5 try/except in fetch")
need("# DEBUG" not in src and "print(" not in src, "6 debug lines")
need(names.index("helper_b") < names.index("helper_a") if "helper_a" in names else False, "7 order")
need(names == ["load_config", "parse_row", "helper_b", "helper_a", "fetch", "export", "is_stale", "run", "reparse"],
     f"no unexpected function changes: {names}")
need(not re.search(r"\n\n\n\n", src) and len(re.findall(r"\n\n\n(?=def )", src)) == 9, "blank-line layout")
need(src.endswith("\n") and not src.endswith("\n\n"), "trailing newline")
print("PASS" if not fails else "FAIL: " + "; ".join(fails)); sys.exit(1 if fails else 0)
