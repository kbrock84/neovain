"""Check a run directory's app/ against the multi-file task. The expected files are in expected/app.

  python3 check.py RUN_DIR

Two scores, as for the other tasks: "layout differs" reasons mean the code matches the expected
syntax tree and only the text differs (line breaks, blank lines, import order).
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

EXPECTED = Path(__file__).resolve().parent / "expected" / "app"
LEFTOVERS = [
    ("1", r"\bfetch_rows\b", "fetch_rows"), ("2", r"\bsend_event\b", "send_event"),
    ("3", r"\blegacy\b", "legacy"), ("4", r"\butcnow\b", "utcnow"), ("5", r"\bdebug_dump\b", "debug_dump"),
]


def shape(src):
    """The syntax tree as text, with the names of a `from` import in a fixed order."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            node.names.sort(key=lambda alias: alias.name)
    return ast.dump(tree)


def comments(src):
    return [tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline) if tok.type == tokenize.COMMENT]


def main():
    got_dir = Path(sys.argv[1]) / "app"
    fails, left, differs, layout = [], {}, [], []
    extra = sorted(p.name for p in got_dir.glob("*.py")) if got_dir.is_dir() else []
    for want_path in sorted(EXPECTED.glob("*.py")):
        name = want_path.name
        got_path = got_dir / name
        if not got_path.is_file():
            fails.append(f"{name} is missing")
            continue
        extra.remove(name)
        got, want = got_path.read_text(), want_path.read_text()
        try:
            got_shape = shape(got)
        except SyntaxError as e:
            fails.append(f"syntax error in {name} line {e.lineno}")
            continue
        for number, pattern, word in LEFTOVERS:
            hits = len(re.findall(pattern, got))
            if hits:
                left[number] = (word, left.get(number, (word, 0))[1] + hits)
        if got_shape != shape(want):
            differs.append(name)
        elif comments(got) != comments(want):
            differs.append(name + " (comments)")
        elif "\n".join(l.rstrip() for l in got.split("\n")) != want:
            g, w = got.split("\n"), want.split("\n")
            k = next((i for i, (x, y) in enumerate(zip(g, w)) if x.rstrip() != y), min(len(g), len(w)))
            layout.append(f"{name} line {k + 1}")
    if extra:
        fails.append("unexpected files: " + ", ".join(extra))
    for number in sorted(left):
        word, hits = left[number]
        fails.append(f"{number} {hits} {word} left")
    if differs:
        fails.append("code differs from expected in " + ", ".join(differs))
    if not fails and layout:
        fails.append(f"layout differs from expected in {len(layout)} file(s): " + ", ".join(layout[:3]))
    print("PASS" if not fails else "FAIL: " + "; ".join(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
