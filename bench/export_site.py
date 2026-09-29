"""Build the generated parts of the site from the committed benchmark results.

  python3 bench/export_site.py          # regenerate
  python3 bench/export_site.py --check  # exit 1 if the site is out of date

Writes site/data/results.json, then puts HTML fragments into site/index.html between
<!-- generated:NAME --> markers. The numbers are in the HTML itself, so the page needs no
JavaScript to show them. The splice is done with neovain.

Inputs: results-v1.jsonl (small task set), results-large.jsonl and demo-large.json.
"""
import html
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent
SITE = BENCH.parent / "site"
INDEX = SITE / "index.html"

# Verified from the run logs: the aliases resolved to claude-opus-5-5 and claude-sonnet-5-5.
MODELS = {"opus": "Opus 5.5", "sonnet": "Sonnet 5.5"}
ARMS = {"edit": "Edit tool", "neovain": "neovain", "neovain-ex": "neovain (ex-only)"}
METRICS = ["out_tok", "think_tok", "api_requests", "tool_calls", "edit_calls", "wall_s", "cost_usd"]
BAR_MAX = 60  # percent of the row, the longest bar in a chart; the rest of the row holds the value

# What each part of the demo command does, matched to the first step with that text.
STEP_NOTES = [
    ("'@def sync_all('", "wrap sync_all in a lock"),
    (r"':%s/\<log_event\>/emit_event/g'", "rename, 191 uses"),
    (r"':g/^def debug_/.,/^\S/-1d'", "delete the debug helpers"),
    ("'@^class LegacyExporter'", "delete LegacyExporter"),
    ("'@^class ReportBuilder'", "move ReportBuilder to the end"),
    ("'@^class CacheLayer'", "swap the two classes"),
]


def load(name):
    return [json.loads(line) for line in (BENCH / name).read_text().splitlines() if line.strip()]


def summary(rows):
    out = {m: statistics.mean(r[m] for r in rows) for m in METRICS}
    out.update(n=len(rows), passed=sum(r["pass"] for r in rows))
    return out


def half_up(value):
    return math.floor(value + 0.5 + 1e-9)


def f_int(value):
    return f"{half_up(value):,}"


def f_one(value):
    return f"{half_up(value * 10) / 10:.1f}"


def f_seconds(value):
    return f"{half_up(value)}s"


def f_usd(value):
    return f"${half_up(value * 100) / 100:.2f}"


def f_ratio(value):
    return f"{half_up(value * 10) / 10:.1f}\u00d7"


def esc(text):
    return html.escape(text, quote=False)


# ---- fragments ----

def demo_fragment(demo, headline):
    words = re.findall(r"""\$'(?:[^'\\]|\\.)*'|'[^']*'|"(?:[^"\\]|\\.)*"|\S+""", demo["neovain_command"])
    head = []
    while words and words[0][0] not in "$'\"":
        head.append(words.pop(0))
    notes = dict(STEP_NOTES)
    steps = [f'<li><code><span class="p">$ </span>{esc(" ".join(head))} \\</code></li>']
    for i, word in enumerate(words):
        text = esc(word) + (" \\" if i < len(words) - 1 else "")
        anchor = ' class="anchor"' if word.lstrip("$'\"").startswith("@") else ""
        note = notes.pop(word, None)
        why = f'<span class="why">{esc(note)}</span>' if note else ""
        steps.append(f"<li><code{anchor}>{text}</code>{why}</li>")

    longest = max(max(c["old_chars"], c["new_chars"]) for c in demo["edit_calls"])

    def width(chars):
        return max(5, round(1000 * chars / longest))

    calls = []
    for i, c in enumerate(demo["edit_calls"], 1):
        calls.append(
            f'<li><span class="n">#{i}</span><span class="what">{esc(c["summary"])}'
            '<svg class="span-bar" viewBox="0 0 1000 12" preserveAspectRatio="none" aria-hidden="true">'
            f'<rect class="find" width="{width(c["old_chars"])}" height="5"/>'
            f'<rect class="repl" y="7" width="{width(c["new_chars"])}" height="5"/></svg></span>'
            f'<span class="chars">{f_int(c["old_chars"] + c["new_chars"])}</span></li>')

    models = " and ".join(MODELS.values())
    lines = [
        '<div class="demo-grid">',
        '  <div class="pane">',
        '    <div class="pane-head"><span class="pane-title"><i class="swatch neovain"></i>neovain</span>'
        f'<span>{demo["neovain_calls"]} calls</span></div>',
        f'    <p class="figure-line"><strong>{f_int(demo["neovain_chars"])}</strong>'
        "<span>characters written by the agent</span></p>",
        '    <ol class="steps">',
        *[f"      {s}" for s in steps],
        "    </ol>",
        '    <p class="pane-foot">The agent previewed the change with <code>--dry-run</code>, then ran it for real'
        " (shown). Anchors are highlighted.</p>",
        "  </div>",
        '  <div class="pane">',
        '    <div class="pane-head"><span class="pane-title"><i class="swatch edit"></i>Edit tool</span>'
        f'<span>{demo["edit_count"]} calls</span></div>',
        f'    <p class="figure-line"><strong>{f_int(demo["edit_chars"])}</strong>'
        "<span>characters written by the agent</span></p>",
        '    <ol class="calls">',
        *[f"      {c}" for c in calls],
        "    </ol>",
        '    <p class="key" aria-hidden="true"><span><i class="find"></i>text to find</span>'
        '<span><i class="repl"></i>replacement text</span></p>',
        '    <p class="pane-foot">String replacement spells out the text it finds and the text it adds, so moving a'
        " class means writing all of it twice.</p>",
        "  </div>",
        "</div>",
        '<dl class="stats">',
        f'  <div><dt>output tokens</dt><dd class="value">{f_ratio(headline["out_ratio"])} fewer</dd>'
        f'<dd class="sub">Mean of {models}, 3 runs each</dd></div>',
        f'  <div><dt>wall time</dt><dd class="value">{f_ratio(headline["time_ratio"])} faster</dd>'
        '<dd class="sub">From prompt to finished file</dd></div>',
        f'  <div><dt>cost</dt><dd class="value">{f_ratio(headline["cost_ratio"])} cheaper</dd>'
        '<dd class="sub">API list price per task</dd></div>',
        "</dl>",
    ]
    return lines


def chart(title, sub, metric, unit, fmt, by_model):
    top = max(by_model[m][a][metric] for m in MODELS for a in ("neovain", "edit"))
    lines = ['<div class="pane chart">', f'  <div><h4>{title}</h4><p class="sub">{sub}</p></div>']
    for m, name in MODELS.items():
        lines += ['  <div class="bar-group">', f"    <span>{name}</span>", '    <div class="bar-rows">']
        for arm in ("neovain", "edit"):
            s = by_model[m][arm]
            value = fmt(s[metric])
            pct = max(1, round(BAR_MAX * s[metric] / top, 1))
            tip = f"{name}, {ARMS[arm]}: {value} {unit}, mean of {s['n']} runs"
            lines.append(
                f'      <div class="bar-row"><svg class="bar {arm}" width="{pct}%" height="14" role="img">'
                f'<title>{esc(tip)}</title><rect width="100%" height="14" rx="2"/></svg><span>{value}</span></div>')
        lines += ["    </div>", "  </div>"]
    lines.append("</div>")
    return lines


def table(rows, with_context):
    head = ["model"] + (["context"] if with_context else []) + ["arm"]
    nums = ["passed", "output tokens", "API requests", "tool calls", "wall time", "cost"]
    lines = ['<div class="table-wrap">', "  <table>", "    <thead><tr>"
             + "".join(f'<th class="text" scope="col">{h}</th>' for h in head)
             + "".join(f'<th scope="col">{h}</th>' for h in nums) + "</tr></thead>", "    <tbody>"]
    for r in rows:
        short = ' class="short"' if r["passed"] < r["n"] else ""
        cells = [f'<th class="text" scope="row">{MODELS[r["model"]]}</th>']
        if with_context:
            cells.append(f'<td class="text ctx">{r["ctx"]}</td>')
        cells += [
            f'<td class="text"><i class="swatch {r["arm"]}"></i>{ARMS[r["arm"]]}</td>',
            f'<td{short}>{r["passed"]}/{r["n"]}</td>',
            f'<td>{f_int(r["out_tok"])}</td>', f'<td>{f_one(r["api_requests"])}</td>',
            f'<td>{f_one(r["tool_calls"])}</td>', f'<td>{f_seconds(r["wall_s"])}</td>',
            f'<td>{f_usd(r["cost_usd"])}</td>',
        ]
        lines.append("      <tr>" + "".join(cells) + "</tr>")
    lines += ["    </tbody>", "  </table>", "</div>"]
    return lines


def grouped(rows, keys, ctx_label):
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r.get(k, 0) for k in keys), []).append(r)
    order = list(MODELS)
    arm_order = list(ARMS)
    out = []
    for key, rs in groups.items():
        g = dict(zip(keys, key))
        out.append({"model": g["model"], "arm": g["arm"], "ctx": ctx_label(g), "ctx_kb": g.get("ctx_kb", 0),
                    **summary(rs)})
    return sorted(out, key=lambda r: (order.index(r["model"]), r["ctx_kb"], arm_order.index(r["arm"])))


def correctness(large):
    runs = {a: [r for r in large if r["arm"] == a] for a in ("neovain", "edit")}
    ok = {a: sum(r["pass"] for r in rs) for a, rs in runs.items()}
    if all(ok[a] == len(runs[a]) for a in runs):
        return "Every run passed the checker."
    return (f"{ok['neovain']} of {len(runs['neovain'])} neovain runs and {ok['edit']} of {len(runs['edit'])} Edit "
            "runs passed. The failed neovain run used a line range that ended at the end of a class instead of the "
            "end of a method: valid Vim, wrong edit, and the agent never read the diff.")


# ---- splice ----

def region(name, lines, indent=8):
    pad = " " * indent
    body = "\n".join(pad + line for line in lines)
    return f"{pad}<!-- generated:{name} -->\n{body}\n{pad}<!-- /generated:{name} -->\n"


def current(name):
    text = INDEX.read_text(encoding="utf-8")
    match = re.search(rf"^[ \t]*<!-- generated:{name} -->\n.*?<!-- /generated:{name} -->\n", text, re.S | re.M)
    if not match:
        raise SystemExit(f"export_site: no generated:{name} region in {INDEX}")
    return match.group(0)


def splice(regions):
    with tempfile.TemporaryDirectory(prefix="neovain-site-") as tmp:
        steps = []
        for name, text in regions.items():
            fragment = Path(tmp) / f"{name}.html"
            fragment.write_text(text, encoding="utf-8", newline="\n")
            steps += [f"@<!-- generated:{name} -->", f":.,/<!-- \\/generated:{name} -->/d",
                      ":-1r " + fragment.as_posix().replace(" ", "\\ ")]
        done = subprocess.run(["neovain", str(INDEX), *steps], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"export_site: neovain failed\n{done.stderr}")


def main():
    large = load("results-large.jsonl")
    small = load("results-v1.jsonl")
    demo = json.loads((BENCH / "demo-large.json").read_text())

    by_model = {m: {a: summary([r for r in large if r["model"] == m and r["arm"] == a])
                    for a in ("edit", "neovain")} for m in MODELS}

    def ratio(metric):
        return statistics.mean(by_model[m]["edit"][metric] / by_model[m]["neovain"][metric] for m in MODELS)

    headline = {"out_ratio": ratio("out_tok"), "time_ratio": ratio("wall_s"), "cost_ratio": ratio("cost_usd")}
    large_rows = grouped(large, ("model", "arm"), lambda g: "2,350 lines")
    small_rows = grouped(small, ("model", "ctx_kb", "arm"), lambda g: "250 KB preload" if g["ctx_kb"] else "none")
    note = correctness(large)

    charts = (['<div class="charts">']
              + ["  " + line for line in chart("Output tokens", "per task", "out_tok", "output tokens", f_int, by_model)]
              + ["  " + line for line in chart("Wall time", "per task, prompt to finished file", "wall_s",
                                               "wall time", f_seconds, by_model)]
              + ["  " + line for line in chart("Cost", "per task at API list price", "cost_usd", "cost", f_usd,
                                               by_model)]
              + ["</div>"])
    regions = {
        "demo": region("demo", demo_fragment(demo, headline)),
        "large": region("large", charts + table(large_rows, False)
                        + [f'<p class="note"><strong>Correctness:</strong> {esc(note)}</p>']),
        "small": region("small", table(small_rows, True)),
    }

    data = {
        "models": {m: f"Claude {name}" for m, name in MODELS.items()},
        "headline": headline, "large": by_model, "large_rows": large_rows, "small_rows": small_rows,
        "correctness": note, "demo": demo,
    }
    data_text = json.dumps(data, indent=1) + "\n"
    data_path = SITE / "data" / "results.json"

    if "--check" in sys.argv:
        stale = [name for name, text in regions.items() if current(name) != text]
        if not data_path.exists() or data_path.read_text(encoding="utf-8") != data_text:
            stale.append("data/results.json")
        if stale:
            raise SystemExit("export_site: out of date: " + ", ".join(stale) + ". Run bench/export_site.py")
        print("export_site: site is up to date")
        return

    for name in regions:
        current(name)  # fail early if a marker is missing
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(data_text, encoding="utf-8", newline="\n")
    splice(regions)
    print("export_site: wrote data/results.json and", ", ".join(regions), "in index.html")
    print("  headline:", ", ".join(f"{k} {f_ratio(v)}" for k, v in headline.items()))


if __name__ == "__main__":
    main()
