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
# The release the site shows. Its tables compare each agent's own tool with this version of neovain.
CURRENT = "0.2.0"
# Batches behind the site's tables: both tools at medium effort, and neovain 0.2.0.
SHOWN = ["claude-medium", "codex-gpt6-medium", "codex-5.6-medium",
         "claude-0.2.0-small", "claude-0.2.0-large", "codex-0.2.0-small", "codex-0.2.0-large"]
# Batches behind the write-up's version tables: everything at medium effort, plus Claude's first round.
HISTORY = SHOWN + ["claude-r1-small", "claude-r1-large", "claude-medium-v2", "codex-medium-v3"]
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


def version(row):
    """What the neovain arm ran with: the release, and for 0.1.0 the guidance it read."""
    if row["arm"] == "edit":
        return None
    return "0.2.0" if row.get("tool") == "0.2.0" else f"0.1.0, guidance {row['guidance']}"


def tool_label(s):
    if s["arm"] == "edit":
        return "Edit tool" if s["agent"] == "claude" else "Patch tool"
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


def pick(rows, model, task, arm, ver=None):
    """One table cell: the runs of one model, task and arm, for the neovain arm of one version."""
    mine = [r for r in rows if r["model"] == model and r["task"] == task and r["arm"] == arm
            and r.get("ctx_kb", 0) == 0 and version(r) == ver]
    if not mine:
        return None
    s = summary(mine)
    s.update(model=mine[0]["model"], model_name=mine[0]["model_name"], arm=arm, agent=mine[0]["agent"],
             guidance=mine[0]["guidance"], version=ver)
    return s


def cells_for(rows, models, task, versions):
    """The edit arm and the chosen versions of the neovain arm, model by model."""
    out = []
    for model in models:
        for arm, ver in [("edit", None)] + [("neovain", v) for v in versions]:
            s = pick(rows, model, task, arm, ver)
            if s:
                out.append(s)
    return out


def discarded(rows, agent, ver):
    mine = [r for r in rows if r["agent"] == agent and r["arm"] == "neovain" and r["task"] == "large"
            and r["valid"] and (r.get("tool") == "0.2.0") == (ver == "0.2.0")]
    return sum(r["edits_discarded"] > 0 for r in mine), len(mine)


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

    thrown = []
    for agent, name in (("claude", "Claude"), ("codex", "Codex")):
        for ver in ("0.1.0", "0.2.0"):
            k, n = discarded(rows, agent, ver)
            thrown.append(f"{name}, neovain {ver}: {k} of {n}")

    items = [
        ("Changed the file with the wrong tool", "These runs are left out of every table.",
         both(lambda r: not r["valid"], valid_only=False)),
        ("Wrong code", "A requested change was missing, incomplete or damaged other code.",
         both(lambda r: not r["code_ok"])),
        ("Right code, wrong blank lines", "Most often two extra blank lines at the end of the file, after "
         "moving a class there.", both(lambda r: r["code_ok"] and not r["pass"])),
        ("Threw neovain's output away", "On the large task: sent it to /dev/null, filtered it, or kept only a "
         "few lines. 0.1.0 printed a 2,000-line diff there; 0.2.0 prints a summary.", thrown),
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


def md_cells(cells, claude, versions=False):
    headers = ["Model", "Tool"] + (["neovain"] if versions else []) + ["Exact", "Code correct", "Output tokens"]
    headers += ["API requests", "Tool calls", "Wall time", "Cost"] if claude else ["Tool calls", "Wall time"]
    rows = []
    for s in cells:
        lead = [short(s["model_name"]), tool_label(s)] + ([s["version"] or ""] if versions else [])
        if not s["n"]:
            rows.append(lead + [f"left out: all {s['excluded']} runs broke the rules"]
                        + [""] * (len(headers) - len(lead) - 1))
            continue
        row = lead + [f"{s['exact']}/{s['n']}", f"{s['code_ok']}/{s['n']}", f_int(s["out_tok"])]
        if claude:
            row.append(f_one(s["api_requests"]))
        row += [f_one(s["tool_calls"]), f_seconds(s["wall_s"])]
        if claude:
            row.append(f_usd(s["cost_usd"]))
        rows.append(row)
    return md_table(headers, rows)


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
    lines = md_table(["Agent", "Tool", "Runs", "Wrong tool (left out)", "Wrong code",
                      "Right code, wrong blank lines", "Scripted its patch"], out)
    thrown = []
    for agent, name in (("claude", "Claude"), ("codex", "Codex")):
        for ver in ("0.1.0", "0.2.0"):
            k, n = discarded(rows, agent, ver)
            thrown.append([name, ver, str(n), str(k)])
    return lines + ["", "Runs on the large task that threw neovain's output away:", ""] + md_table(
        ["Agent", "neovain", "Runs", "Threw the output away"], thrown)


def totals_md(rows, models, task, versions):
    """One row per tool and version, over every model: what a change of version did overall."""
    out = []
    for arm, ver in [("edit", None)] + [("neovain", v) for v in versions]:
        mine = [r for r in rows if r["model"] in models and r["task"] == task and r["arm"] == arm
                and r.get("ctx_kb", 0) == 0 and version(r) == ver]
        ok = [r for r in mine if r["valid"]]
        out.append(["Patch tool" if arm == "edit" else f"neovain {ver}", str(len(ok)), str(len(mine) - len(ok)),
                    str(sum(r["pass"] for r in ok)), str(sum(r["code_ok"] for r in ok)),
                    str(sum(r["code_ok"] and not r["pass"] for r in ok)), str(sum(not r["code_ok"] for r in ok)),
                    f_int(statistics.mean(r["out_tok"] for r in ok)),
                    f_seconds(statistics.mean(r["wall_s"] for r in ok))])
    return md_table(["Tool", "Runs", "Left out", "Exact", "Code correct", "Right code, wrong blank lines",
                     "Wrong code", "Output tokens", "Wall time"], out)


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
        done = subprocess.run(["neovain", "--diff", "summary", str(path), *steps], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"export_site: neovain failed on {path.name}\n{done.stderr}")


def main():
    rows = [json.loads(line) for line in (BENCH / "results.jsonl").read_text().splitlines() if line.strip()]
    demo = json.loads((BENCH / "demo-large.json").read_text())

    shown = [r for r in rows if r["batch"] in SHOWN]
    large_cells = cells_for(shown, CLAUDE, "large", [CURRENT])
    small = cells_for(shown, CLAUDE, "small", [CURRENT])
    large = {m: {s["arm"]: s for s in large_cells if s["model"] == m} for m in CLAUDE}

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
    codex_large = cells_for(shown, CODEX, "large", [CURRENT])
    codex_small = cells_for(shown, CODEX, "small", [CURRENT])

    regions = {
        "demo": region("demo", demo_fragment(demo, headline)),
        "large": region("large", charts + table(large_cells, True)),
        "small": region("small", table(small, True)),
        "codex": region("codex", ['<h4 class="table-title">Large structural edits</h4>'] + table(codex_large, False)
                        + ['<h4 class="table-title">Small edits</h4>'] + table(codex_small, False)),
        "behavior": region("behavior", behavior(rows)),
    }

    history = [r for r in rows if r["batch"] in HISTORY]
    claude_versions = ["0.1.0, guidance v1", "0.1.0, guidance v2", "0.1.0, guidance v3", "0.2.0"]
    codex_versions = ["0.1.0, guidance v2", "0.1.0, guidance v3", "0.2.0"]

    def neovain_only(cells):
        return [s for s in cells if s["arm"] == "neovain"]

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
                           + md_cells(neovain_only(cells_for(history, CLAUDE, "large", claude_versions)), True, True)
                           + ["", "**Small edits**", ""]
                           + md_cells(neovain_only(cells_for(history, CLAUDE, "small", claude_versions)), True, True),
                           0),
        "codex": region("codex", ["**Large structural edits**", ""]
                        + md_cells(cells_for(history, CODEX, "large", codex_versions), False, True)
                        + ["", "**Small edits**", ""]
                        + md_cells(cells_for(history, CODEX, "small", codex_versions), False, True)
                        + ["", "**All seven models together, large structural edits**", ""]
                        + totals_md(history, CODEX, "large", codex_versions)
                        + ["", "**All seven models together, small edits**", ""]
                        + totals_md(history, CODEX, "small", codex_versions), 0),
        "behavior": region("behavior", behavior_md(rows), 0),
    }
    readme_path = BENCH / "README.md"

    data = {"headline": headline, "neovain": CURRENT, "claude_large": large_cells, "claude_small": small,
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
