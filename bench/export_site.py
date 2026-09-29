"""Build site/data/results.json from the committed benchmark results.

  python3 bench/export_site.py

Inputs: results-v1.jsonl (small task set), results-large.jsonl and demo-large.json.
"""
import json
import statistics
from pathlib import Path

BENCH = Path(__file__).resolve().parent
OUT = BENCH.parent / "site" / "data" / "results.json"
MODELS = ["opus", "sonnet"]
METRICS = ["out_tok", "think_tok", "api_requests", "tool_calls", "edit_calls", "wall_s", "cost_usd"]
ARM_NAMES = {"edit": "Edit", "neovain": "neovain", "neovain-ex": "neovain (ex-only)"}


def load(name):
    return [json.loads(line) for line in (BENCH / name).read_text().splitlines() if line.strip()]


def summary(rows):
    out = {m: round(statistics.mean(r[m] for r in rows), 3) for m in METRICS}
    out.update(n=len(rows), passed=sum(r["pass"] for r in rows))
    return out


def table_rows(rows, keys, ctx_label):
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r.get(k, 0) for k in keys), []).append(r)
    result = []
    for key, rs in sorted(groups.items(), key=lambda kv: (MODELS.index(kv[0][0]), kv[0][1:])):
        s = summary(rs)
        g = dict(zip(keys, key))
        result.append({"model": g["model"], "arm": g["arm"], "ctx": ctx_label(g),
                       "pass": f"{s['passed']}/{s['n']}", **{m: s[m] for m in METRICS}})
    return result


def main():
    large = load("results-large.jsonl")
    small = load("results-v1.jsonl")
    demo = json.loads((BENCH / "demo-large.json").read_text())

    by_model = {m: {a: summary([r for r in large if r["model"] == m and r["arm"] == a])
                    for a in ("edit", "neovain")} for m in MODELS}

    def ratio(metric):
        return round(statistics.mean(by_model[m]["edit"][metric] / by_model[m]["neovain"][metric] for m in MODELS), 2)

    runs = {a: [r for r in large if r["arm"] == a] for a in ("edit", "neovain")}
    fails = {a: sum(not r["pass"] for r in rs) for a, rs in runs.items()}
    if any(fails.values()):
        note = (f"Correctness: {len(runs['neovain']) - fails['neovain']} of {len(runs['neovain'])} neovain runs "
                f"and {len(runs['edit']) - fails['edit']} of {len(runs['edit'])} Edit runs passed. The failed "
                "neovain run used a line range that ended at the end of a class instead of the end of a method: "
                "valid Vim, wrong edit, and the agent never read the diff.")
    else:
        note = "Every run passed the checker."

    data = {
        "large": by_model,
        "headline": {"out_ratio": ratio("out_tok"), "time_ratio": ratio("wall_s"), "cost_ratio": ratio("cost_usd")},
        "large_rows": table_rows(large, ("model", "arm"), lambda g: "2,350 lines"),
        "small_rows": table_rows(small, ("model", "ctx_kb", "arm"),
                                 lambda g: "250 KB preload" if g["ctx_kb"] else "none"),
        "large_fail_note": note,
        "demo": demo,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n", newline="\n")
    print(f"wrote {OUT.relative_to(BENCH.parent)}: headline {data['headline']}")


if __name__ == "__main__":
    main()
