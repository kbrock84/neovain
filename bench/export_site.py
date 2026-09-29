"""Build the generated parts of the site from the collected benchmark results.

  python3 bench/export_site.py          # regenerate
  python3 bench/export_site.py --check  # exit 1 if the site is out of date

Writes site/data/results.json, then puts HTML fragments into site/index.html between
<!-- generated:NAME --> markers. The numbers are in the HTML itself, so the page needs no
JavaScript to show them. The splice is done with neovain.

Inputs: results.jsonl (every run, written by collect.py) and demo-large.json. Runs that broke
the rules (valid is false) are left out of every table and counted in the behavior section.
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

CLAUDE = ["opus", "sonnet"]
CODEX = ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]
CODEX_BATCHES = ["codex-gpt6-medium", "codex-5.6-medium", "codex-medium-v3"]
METRICS = ["out_tok", "think_tok", "tool_calls", "edit_calls", "wall_s"]
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


def short(name):
    return name.removeprefix("Claude ")


def summary(rows):
    """Means over the valid runs of one cell, plus the two scores and the number left out."""
    ok = [r for r in rows if r["valid"]]
    out = {"n": len(ok), "excluded": len(rows) - len(ok)}
    if not ok:
        return out
    out.update({m: statistics.mean(r[m] for r in ok) for m in METRICS})
    for m in ("cost_usd", "api_requests"):
        vals = [r[m] for r in ok if r[m] is not None]
        out[m] = statistics.mean(vals) if vals else None
    out.update(exact=sum(r["pass"] for r in ok), code_ok=sum(r["code_ok"] for r in ok),
               model=rows[0]["model"], model_name=rows[0]["model_name"], arm=rows[0]["arm"],
               agent=rows[0]["agent"], guidance=rows[0]["guidance"])
    return out


def cell(rows, **want):
    return summary([r for r in rows if all(r[k] == v for k, v in want.items())])


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

    return [
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
        f'<dd class="sub">{esc(headline["basis"])}</dd></div>',
        f'  <div><dt>wall time</dt><dd class="value">{f_ratio(headline["time_ratio"])} faster</dd>'
        '<dd class="sub">From prompt to finished file</dd></div>',
        f'  <div><dt>cost</dt><dd class="value">{f_ratio(headline["cost_ratio"])} cheaper</dd>'
        '<dd class="sub">API list price per task</dd></div>',
        "</dl>",
    ]


def chart(title, sub, metric, unit, fmt, cells):
    top = max(c[metric] for pair in cells.values() for c in pair.values())
    lines = ['<div class="pane chart">', f'  <div><h4>{title}</h4><p class="sub">{sub}</p></div>']
    for pair in cells.values():
        name = short(pair["edit"]["model_name"])
        lines += ['  <div class="bar-group">', f"    <span>{name}</span>", '    <div class="bar-rows">']
        for arm, label in (("neovain", "neovain"), ("edit", "Edit tool")):
            s = pair[arm]
            value = fmt(s[metric])
            pct = max(1, round(BAR_MAX * s[metric] / top, 1))
            tip = f"{name}, {label}: {value} {unit}, mean of {s['n']} runs"
            lines.append(
                f'      <div class="bar-row"><svg class="bar {arm}" width="{pct}%" height="14" role="img">'
                f'<title>{esc(tip)}</title><rect width="100%" height="14" rx="2"/></svg><span>{value}</span></div>')
        lines += ["    </div>", "  </div>"]
    lines.append("</div>")
    return lines


def tool_label(s):
    if s["arm"] == "edit":
        return "Edit tool" if s["agent"] == "claude" else "Patch tool"
    if s["agent"] == "codex":
        return f"neovain, guidance {s['guidance']}"
    return "neovain"


def score(done, n):
    flag = ' class="short"' if done < n else ""
    return f"<td{flag}>{done}/{n}</td>"


def table(cells, claude):
    nums = ["exact", "code correct", "output tokens"]
    nums += ["API requests", "tool calls", "wall time", "cost"] if claude else ["tool calls", "wall time"]
    lines = ['<div class="table-wrap">', "  <table>",
             '    <thead><tr><th class="text" scope="col">model</th><th class="text" scope="col">tool</th>'
             + "".join(f'<th scope="col">{h}</th>' for h in nums) + "</tr></thead>", "    <tbody>"]
    for s in cells:
        name = f'<th class="text" scope="row">{short(s["model_name"])}</th>'
        tool = f'<td class="text"><i class="swatch {s["arm"]}"></i>{tool_label(s)}</td>'
        if not s["n"]:
            span = len(nums)
            lines.append(f'      <tr>{name}{tool}<td class="text ctx" colspan="{span}">'
                         f'left out: all {s["excluded"]} runs broke the rules</td></tr>')
            continue
        cells_html = [score(s["exact"], s["n"]), score(s["code_ok"], s["n"]), f'<td>{f_int(s["out_tok"])}</td>']
        if claude:
            cells_html.append(f'<td>{f_one(s["api_requests"])}</td>')
        cells_html += [f'<td>{f_one(s["tool_calls"])}</td>', f'<td>{f_seconds(s["wall_s"])}</td>']
        if claude:
            cells_html.append(f'<td>{f_usd(s["cost_usd"])}</td>')
        lines.append("      <tr>" + name + tool + "".join(cells_html) + "</tr>")
    lines += ["    </tbody>", "  </table>", "</div>"]
    return lines


def with_excluded(rows, s, **want):
    """A cell with no valid runs still needs its labels for the table row."""
    if s["n"]:
        return s
    any_row = next(r for r in rows if all(r[k] == v for k, v in want.items()))
    s.update(model=any_row["model"], model_name=any_row["model_name"], arm=any_row["arm"],
             agent=any_row["agent"], guidance=any_row["guidance"])
    return s


def codex_cells(rows, task):
    rows = [r for r in rows if r["batch"] in CODEX_BATCHES and r["task"] == task]
    cells = []
    for model in CODEX:
        for arm, guidance in (("edit", None), ("neovain", "v2"), ("neovain", "v3")):
            want = dict(model=model, arm=arm, guidance=guidance)
            if any(all(r[k] == v for k, v in want.items()) for r in rows):
                cells.append(with_excluded(rows, cell(rows, **want), **want))
    return cells


def behavior(rows):
    def count(pred, where):
        hit = [r for r in rows if where(r)]
        return sum(1 for r in hit if pred(r)), len(hit)

    def both(pred, valid_only=True):
        out = []
        for agent, name in (("claude", "Claude"), ("codex", "Codex")):
            for arm, tool in (("edit", "own tool"), ("neovain", "neovain")):
                k, n = count(pred, lambda r: r["agent"] == agent and (r["arm"] == "edit") == (arm == "edit")
                             and (r["valid"] or not valid_only))
                out.append(f"{name}, {tool}: {k} of {n}")
        return out

    items = [
        ("Changed the file with the wrong tool", "These runs are left out of every table.",
         both(lambda r: not r["valid"], valid_only=False)),
        ("Wrong code", "A requested change was missing, incomplete or damaged other code.",
         both(lambda r: not r["code_ok"])),
        ("Right code, wrong blank lines", "Most often two extra blank lines at the end of the file, after "
         "moving a class there.", both(lambda r: r["code_ok"] and not r["pass"])),
        ("Wrote a script to work out its patch", "Within the rules: the agent still applied the patch with its "
         "own tool. It helps explain the low token counts.",
         both(lambda r: r.get("scripted_patch", False))[::2]),
    ]
    lines = ['<div class="table-wrap">', "  <table>",
             '    <thead><tr><th class="text" scope="col">what happened</th>'
             '<th class="text" scope="col">runs</th></tr></thead>', "    <tbody>"]
    for title, note, counts in items:
        lines.append(f'      <tr><th class="text wrap" scope="row"><strong>{esc(title)}</strong><br>{esc(note)}</th>'
                     f'<td class="text">{"<br>".join(esc(c) for c in counts)}</td></tr>')
    lines += ["    </tbody>", "  </table>", "</div>"]
    return lines


# ---- the write-up (bench/README.md) ----

def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    return lines + ["| " + " | ".join(r) + " |" for r in rows]


def md_cells(cells, claude, guidance=False):
    headers = ["Model", "Tool"] + (["Guidance"] if guidance else []) + ["Exact", "Code correct", "Output tokens"]
    headers += ["API requests", "Tool calls", "Wall time", "Cost"] if claude else ["Tool calls", "Wall time"]
    rows = []
    for s in cells:
        tool = ("Edit tool" if s["agent"] == "claude" else "Patch tool") if s["arm"] == "edit" else "neovain"
        lead = [short(s["model_name"]), tool] + ([s["guidance"] or ""] if guidance else [])
        if not s["n"]:
            rows.append(lead + [f"left out: all {s['excluded']} runs broke the rules"] + [""] * (len(headers) - len(lead) - 1))
            continue
        row = lead + [f"{s['exact']}/{s['n']}", f"{s['code_ok']}/{s['n']}", f_int(s["out_tok"])]
        if claude:
            row.append(f_one(s["api_requests"]))
        row += [f_one(s["tool_calls"]), f_seconds(s["wall_s"])]
        if claude:
            row.append(f_usd(s["cost_usd"]))
        rows.append(row)
    return md_table(headers, rows)


def guidance_cells(rows, task):
    """Claude's neovain arm under each version of the guidance. v1 ran with the effort left to the CLI."""
    picks = [("v1", "claude-r1-large" if task == "large" else "claude-r1-small"),
             ("v2", "claude-medium-v2"), ("v3", "claude-medium")]
    return [cell(rows, batch=batch, task=task, model=m, arm="neovain", ctx_kb=0)
            for m in CLAUDE for _, batch in picks]


def behavior_md(rows):
    groups = [("Claude", "claude", "edit", "Edit tool"), ("Claude", "claude", "neovain", "neovain"),
              ("Codex", "codex", "edit", "Patch tool"), ("Codex", "codex", "neovain", "neovain")]
    out = []
    for name, agent, arm, tool in groups:
        mine = [r for r in rows if r["agent"] == agent and (r["arm"] == "edit") == (arm == "edit")]
        ok = [r for r in mine if r["valid"]]
        out.append([name, tool, str(len(mine)), str(len(mine) - len(ok)), str(sum(not r["code_ok"] for r in ok)),
                    str(sum(r["code_ok"] and not r["pass"] for r in ok)),
                    str(sum(r.get("scripted_patch", False) for r in ok)) if arm == "edit" else ""])
    return md_table(["Agent", "Tool", "Runs", "Wrong tool (left out)", "Wrong code", "Right code, wrong blank lines",
                     "Scripted its patch"], out)


# ---- splice ----

def region(name, lines, indent=8):
    pad = " " * indent
    body = "\n".join((pad + line).rstrip() for line in lines)
    return f"{pad}<!-- generated:{name} -->\n{body}\n{pad}<!-- /generated:{name} -->\n"


def current(name, path):
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^[ \t]*<!-- generated:{name} -->\n.*?<!-- /generated:{name} -->\n", text, re.S | re.M)
    if not match:
        raise SystemExit(f"export_site: no generated:{name} region in {path}")
    return match.group(0)


def splice(regions, path):
    with tempfile.TemporaryDirectory(prefix="neovain-site-") as tmp:
        steps = []
        for name, text in regions.items():
            fragment = Path(tmp) / f"{name}.txt"
            fragment.write_text(text, encoding="utf-8", newline="\n")
            steps += [f"@<!-- generated:{name} -->", f":.,/<!-- \\/generated:{name} -->/d",
                      ":-1r " + fragment.as_posix().replace(" ", "\\ ")]
        done = subprocess.run(["neovain", str(path), *steps], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"export_site: neovain failed on {path.name}\n{done.stderr}")


def main():
    rows = [json.loads(line) for line in (BENCH / "results.jsonl").read_text().splitlines() if line.strip()]
    demo = json.loads((BENCH / "demo-large.json").read_text())

    claude = [r for r in rows if r["batch"] == "claude-medium"]
    large = {m: {a: cell(claude, task="large", model=m, arm=a) for a in ("edit", "neovain")} for m in CLAUDE}
    small = [cell(claude, task="small", model=m, arm=a) for m in CLAUDE for a in ("edit", "neovain")]

    def ratio(metric):
        return statistics.mean(large[m]["edit"][metric] / large[m]["neovain"][metric] for m in CLAUDE)

    names = " and ".join(short(large[m]["edit"]["model_name"]) for m in CLAUDE)
    headline = {"out_ratio": ratio("out_tok"), "time_ratio": ratio("wall_s"), "cost_ratio": ratio("cost_usd"),
                "basis": f"Mean of Claude {names}, 3 runs each"}

    charts = (['<div class="charts">']
              + ["  " + line for line in chart("Output tokens", "per task", "out_tok", "output tokens", f_int, large)]
              + ["  " + line for line in chart("Wall time", "per task, prompt to finished file", "wall_s",
                                               "wall time", f_seconds, large)]
              + ["  " + line for line in chart("Cost", "per task at API list price", "cost_usd", "cost", f_usd, large)]
              + ["</div>"])
    large_cells = [large[m][a] for m in CLAUDE for a in ("edit", "neovain")]
    codex_large, codex_small = codex_cells(rows, "large"), codex_cells(rows, "small")
    have = sorted({r["batch"] for r in rows if r["batch"] in CODEX_BATCHES})
    waiting = [b for b in CODEX_BATCHES if b not in have]
    partial = [f"{b} ({sum(r['batch'] == b for r in rows)} runs so far)" for b in have
               if b == "codex-5.6-medium" and sum(r["batch"] == b for r in rows) < 36]
    status = ""
    if waiting or partial:
        status = ('<p class="note"><strong>Still running:</strong> '
                  + esc("; ".join(partial + [f"{b} (not started)" for b in waiting]))
                  + ". This table will grow as those runs finish.</p>")

    regions = {
        "demo": region("demo", demo_fragment(demo, headline)),
        "large": region("large", charts + table(large_cells, True)),
        "small": region("small", table(small, True)),
        "codex": region("codex", ['<h4 class="table-title">Large structural edits</h4>'] + table(codex_large, False)
                        + ['<h4 class="table-title">Small edits</h4>'] + table(codex_small, False)
                        + ([status] if status else [])),
        "behavior": region("behavior", behavior(rows)),
    }

    summary_line = (f"{len(rows)} runs in {len({r['batch'] for r in rows})} batches. "
                    f"{sum(not r['valid'] for r in rows)} runs broke the rules and are left out of the tables.")
    readme = {
        "summary": region("summary", [summary_line], 0),
        "claude": region("claude", ["**Large structural edits**", ""] + md_cells(large_cells, True)
                         + ["", "**Small edits**", ""] + md_cells(small, True)
                         + ["", f"On the large task neovain used {f_ratio(headline['out_ratio'])} fewer output tokens, "
                            f"took {f_ratio(headline['time_ratio'])} less time and cost "
                            f"{f_ratio(headline['cost_ratio'])} less (means of the two models' ratios)."], 0),
        "guidance": region("guidance", ["**Large structural edits**", ""]
                           + md_cells(guidance_cells(rows, "large"), True, guidance=True)
                           + ["", "**Small edits**", ""]
                           + md_cells(guidance_cells(rows, "small"), True, guidance=True), 0),
        "codex": region("codex", ["**Large structural edits**", ""] + md_cells(codex_large, False, guidance=True)
                        + ["", "**Small edits**", ""] + md_cells(codex_small, False, guidance=True)
                        + (["", "Still running: " + "; ".join(partial + [f"{b} (not started)" for b in waiting]) + "."]
                           if waiting or partial else []), 0),
        "behavior": region("behavior", behavior_md(rows), 0),
    }
    readme_path = BENCH / "README.md"

    data = {"headline": headline, "claude_large": large_cells, "claude_small": small,
            "codex_large": codex_large, "codex_small": codex_small, "demo": demo,
            "runs": len(rows), "runs_left_out": sum(not r["valid"] for r in rows)}
    data_text = json.dumps(data, indent=1) + "\n"
    data_path = SITE / "data" / "results.json"

    if "--check" in sys.argv:
        stale = [f"index.html:{name}" for name, text in regions.items() if current(name, INDEX) != text]
        stale += [f"README.md:{name}" for name, text in readme.items() if current(name, readme_path) != text]
        if not data_path.exists() or data_path.read_text(encoding="utf-8") != data_text:
            stale.append("data/results.json")
        if stale:
            raise SystemExit("export_site: out of date: " + ", ".join(stale) + ". Run bench/export_site.py")
        print("export_site: site and write-up are up to date")
        return

    for name in regions:
        current(name, INDEX)  # fail early if a marker is missing
    for name in readme:
        current(name, readme_path)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(data_text, encoding="utf-8", newline="\n")
    splice(regions, INDEX)
    splice(readme, readme_path)
    print("export_site: wrote data/results.json,", ", ".join(regions), "in index.html and",
          ", ".join(readme), "in bench/README.md")
    print("  headline:", f_ratio(headline["out_ratio"]), f_ratio(headline["time_ratio"]), f_ratio(headline["cost_ratio"]))


if __name__ == "__main__":
    main()
