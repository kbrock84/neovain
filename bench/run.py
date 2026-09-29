#!/usr/bin/env python3
"""Run the neovain-vs-Edit benchmark matrix with headless `claude -p` and summarize it.

  python3 run.py [--models opus,sonnet] [--arms edit,neovain,neovain-ex] [--context-kb 0,400]
                 [--reps 3] [-j 3] [--out runs] [--bin neovain]
  python3 run.py --summarize-only --out runs

Arms:
  edit        modify work.py only with Claude Code's Edit tool (exact string replacement)
  neovain     modify work.py only through the neovain CLI
  neovain-ex  neovain with NEOVAIN_EX_ONLY=1 (anchors and ex commands only, no normal-mode keys)

--context-kb N preloads N KB of real source code (Neovim's Lua runtime) into the prompt so every
turn carries a large context, as in a long real session. The prompt is sent on stdin.

Each run gets a fresh copy of fixture.py as work.py. Results are appended to <out>/results.jsonl,
and a per-(model, arm, context) summary table is printed at the end.
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
README = HERE.parent / "README.md"

COMMON = """Apply the edits described in TASKS.md (in the current directory) to work.py.
Reading is unrestricted (Read, Grep, Glob, cat -n, rg). Do not look at any other file except as stated.
You may check syntax with: python3 -c "import ast;ast.parse(open('work.py').read())"
When done, reply with just DONE."""


def prompts(binary: str) -> dict:
    tool = f"""You must modify work.py ONLY by running the neovain CLI through Bash:
  {binary} work.py STEP...
Read {README} first to learn it. Do not write work.py any other way
(no Edit/Write tools, no sed -i, no redirection, no scripts)."""
    return {
        "edit": f"""{COMMON}
You must modify work.py ONLY with the Edit tool (exact string replacement). Do not use Write,
sed, redirection or scripts to change the file.""",
        "neovain": f"{COMMON}\n{tool}",
        "neovain-ex": f"""{COMMON}
{tool}
Ex-only mode is enabled: only @anchor and :ex steps are accepted. Normal-mode keys and :normal are
rejected. Insert new lines with ex commands such as :call append(line('.'), ['line 1', 'line 2'])
or :s with \\r in the replacement.""",
    }


TOOLS = {
    "edit": (["Edit", "Bash", "Read", "Grep", "Glob"], ["Write", "NotebookEdit", "Agent"]),
    "neovain": (["Bash", "Read", "Grep", "Glob"], ["Edit", "Write", "NotebookEdit", "Agent"]),
    "neovain-ex": (["Bash", "Read", "Grep", "Glob"], ["Edit", "Write", "NotebookEdit", "Agent"]),
}

NEOVAIN_CALL = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?neovain(?:\s|$)")
# Bash commands that change work.py without going through neovain break the rules of either arm.
ILLEGAL_WRITE = re.compile(r"(sed\s+-i|>\s*work\.py|tee\s+work\.py|open\([^)]*work\.py[^)]*['\"][wa])")


def preload(kb: int) -> str:
    """Deterministic ~kb KB of real Lua source from Neovim's runtime, framed as background."""
    if kb <= 0:
        return ""
    nvim = shutil.which("nvim")
    runtime = Path(os.path.realpath(nvim)).parent.parent / "share" / "nvim" / "runtime" / "lua"
    chunks, size = [], 0
    for f in sorted(runtime.rglob("*.lua")):
        text = f.read_text(errors="replace")
        chunks.append(f"===== {f.relative_to(runtime)} =====\n{text}")
        size += len(text)
        if size >= kb * 1024:
            break
    return ("Background: the source files below are from a project you have been reading in this session. "
            "They are not needed for the task that follows.\n\n" + "\n".join(chunks) + "\n\n===== END OF BACKGROUND =====\n\n")


def run_one(a, model: str, arm: str, kb: int, rep: int, background: dict) -> dict:
    d = a.out / f"{model}_{arm}_ctx{kb}_{rep}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copy(HERE / "fixture.py", d / "work.py")
    shutil.copy(HERE / "TASKS.md", d / "TASKS.md")
    allowed, denied = TOOLS[arm]
    cmd = ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose",
           "--allowedTools", *allowed, "--disallowedTools", *denied, "--max-turns", "40"]
    env = {**os.environ, "NEOVAIN_EX_ONLY": "1" if arm == "neovain-ex" else "0"}
    prompt = background[kb] + prompts(a.bin)[arm]
    t0 = time.monotonic()
    with open(d / "stream.jsonl", "w") as f:
        proc = subprocess.run(cmd, cwd=d, input=prompt, stdout=f, stderr=subprocess.PIPE, text=True,
                              timeout=1200, env=env)
    wall = time.monotonic() - t0
    check = subprocess.run(["python3", str(HERE / "check.py"), str(d / "work.py")], capture_output=True, text=True)
    row = {"model": model, "arm": arm, "ctx_kb": kb, "rep": rep, "wall_s": round(wall, 1),
           "pass": check.returncode == 0, "check": check.stdout.strip(), "exit": proc.returncode}
    row.update(parse_stream(d / "stream.jsonl"))
    if proc.returncode != 0:
        row["stderr"] = proc.stderr[-500:]
    return row


def parse_stream(path: Path) -> dict:
    tool_counts, edit_calls, edit_failed, violations = {}, 0, 0, 0
    pending = {}  # tool_use id -> is edit call
    msg_ids = set()  # distinct model responses = real API round trips
    result = {}
    for line in path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
        msg = ev.get("message") or {}
        if ev.get("type") == "assistant" and msg.get("id"):
            msg_ids.add(msg["id"])
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                name = b["name"]
                tool_counts[name] = tool_counts.get(name, 0) + 1
                command = b["input"].get("command", "") if name == "Bash" else ""
                calls_neovain = bool(NEOVAIN_CALL.search(command))
                is_edit = name == "Edit" or calls_neovain
                if not calls_neovain and ILLEGAL_WRITE.search(command):
                    violations += 1
                edit_calls += is_edit
                pending[b["id"]] = is_edit
            elif b.get("type") == "tool_result" and pending.get(b.get("tool_use_id")):
                content = b.get("content")
                text = json.dumps(content) if not isinstance(content, str) else content
                if b.get("is_error") or "FAILED at step" in text:
                    edit_failed += 1
    usage = result.get("usage", {})
    return {
        "out_tok": usage.get("output_tokens"),
        "think_tok": (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
        "cache_read_tok": usage.get("cache_read_input_tokens"),
        "cache_write_tok": usage.get("cache_creation_input_tokens"),
        "cost_usd": result.get("total_cost_usd"),
        "turns": result.get("num_turns"),
        "api_requests": len(msg_ids),
        "api_s": round((result.get("duration_api_ms") or 0) / 1000, 1),
        "tool_calls": sum(tool_counts.values()),
        "tools": tool_counts,
        "edit_calls": edit_calls,
        "edit_failed": edit_failed,
        "violations": violations,
        "subtype": result.get("subtype"),
        "api_error": result.get("result") if result.get("is_error") or not result else None,
    }


def summarize(rows: list[dict]) -> None:
    def ms(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return "-"
        m = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0
        if m >= 10000:
            return f"{m / 1000:.0f}k±{sd / 1000:.0f}k"
        return f"{m:.0f}±{sd:.0f}" if m >= 10 else f"{m:.2f}±{sd:.2f}"

    errored = [r for r in rows if r.get("api_error")]
    if errored:
        print(f"excluded {len(errored)} run(s) that hit API/auth errors")
    rows = [r for r in rows if not r.get("api_error")]
    if not rows:
        return
    groups = {}
    for r in rows:
        groups.setdefault((r["model"], r.get("ctx_kb", 0), r["arm"]), []).append(r)
    cols = ["model", "ctx_kb", "arm", "n", "pass", "out_tok", "think_tok", "turns", "api_requests", "tool_calls", "edit_calls",
            "edit_failed", "violations", "cache_read_tok", "wall_s", "cost_usd"]
    table = []
    for (model, kb, arm), rs in sorted(groups.items()):
        table.append({"model": model, "ctx_kb": str(kb), "arm": arm, "n": str(len(rs)),
                      "pass": f"{sum(r['pass'] for r in rs)}/{len(rs)}",
                      **{c: ms([r.get(c) for r in rs]) for c in cols[5:]}})
    w = {c: max(len(c), *(len(t[c]) for t in table)) for c in cols}
    print("  ".join(c.ljust(w[c]) for c in cols))
    for t in table:
        print("  ".join(t[c].ljust(w[c]) for c in cols))


def preflight(binary: str) -> None:
    """Fail fast if claude can't reach the API or neovain isn't runnable, instead of recording bogus FAILs."""
    if not shutil.which(binary):
        raise SystemExit(f"preflight: {binary!r} not found on PATH (cargo install --path .. or pass --bin)")
    p = subprocess.run(["claude", "-p", "Reply OK", "--model", "haiku", "--output-format", "json"],
                       capture_output=True, text=True, timeout=120)
    try:
        r = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"preflight: claude produced no JSON:\n{p.stdout[-500:]}{p.stderr[-500:]}")
    if r.get("is_error"):
        raise SystemExit(f"preflight: claude -p failed: {r.get('result')}\n"
                         "Run `claude` once interactively and complete /login, then retry.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="opus,sonnet")
    ap.add_argument("--arms", default="edit,neovain,neovain-ex")
    ap.add_argument("--context-kb", default="0", help="comma-separated preload sizes in KB, e.g. 0,400")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("-j", "--jobs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=HERE / "runs")
    ap.add_argument("--bin", default="neovain", help="neovain binary name or path")
    ap.add_argument("--summarize-only", action="store_true")
    a = ap.parse_args()
    results = a.out / "results.jsonl"
    if not a.summarize_only:
        preflight(a.bin)
        a.out.mkdir(parents=True, exist_ok=True)
        sizes = [int(k) for k in a.context_kb.split(",")]
        background = {kb: preload(kb) for kb in sizes}
        jobs = [(m, arm, kb, r) for r in range(1, a.reps + 1) for kb in sizes
                for m in a.models.split(",") for arm in a.arms.split(",")]
        with ThreadPoolExecutor(a.jobs) as ex, open(results, "a") as f:
            for row in ex.map(lambda j: run_one(a, *j, background), jobs):
                f.write(json.dumps(row) + "\n")
                f.flush()
                tag = f"{row['model']:6} ctx{row['ctx_kb']:<4} {row['arm']:10} #{row['rep']}"
                if row["api_error"]:
                    print(f"{tag}  ERROR  {row['api_error']}", flush=True)
                    continue
                print(f"{tag}  {'PASS' if row['pass'] else 'FAIL'}  out={row['out_tok']} "
                      f"tools={row['tool_calls']} edits={row['edit_calls']} failed={row['edit_failed']} "
                      f"wall={row['wall_s']}s ${row['cost_usd']:.3f}", flush=True)
    summarize([json.loads(l) for l in results.read_text().splitlines() if l.strip()])


if __name__ == "__main__":
    main()
