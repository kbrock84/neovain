#!/usr/bin/env python3
"""Run the neovain-vs-Edit benchmark matrix with headless `claude -p` and summarize it.

  python3 run.py [--agent codex] [--effort medium] [--models opus,sonnet] [--arms edit,neovain,neovain-ex] [--task small,large] [--context-kb 0,250]
                 [--reps 3] [-j 3] [--out runs] [--bin neovain]
  python3 run.py --summarize-only --out runs

Arms:
  edit        modify work.py only with Claude Code's Edit tool (exact string replacement)
  neovain     modify work.py only through the neovain CLI
  neovain-ex  neovain with NEOVAIN_EX_ONLY=1 (anchors and ex commands only, no normal-mode keys)

--context-kb N preloads N KB of real source code (Neovim's Lua runtime) into the prompt so every
turn carries a large context, as in a long real session. The prompt is sent on stdin.

Each run gets a fresh copy of tasks/TASK/fixture.py as work.py. Results are appended to <out>/results.jsonl,
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

NEOVAIN_CALL = re.compile(r"""(?:^|[\s;&|('"])(?:\S*/)?neovain(?:\s|$)""")
# Shell commands that write a file. The first version of this check knew only sed -i, redirects and
# open(..., "w"), and missed a model that rewrote work.py with Path.write_text and piped patches.
# A script that only prints a patch is not a write: the agent still has to apply it with its tool.
SHELL_WRITE = re.compile(
    r"sed\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*i"
    r"|perl\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*i"
    r"|>>?\s*\S*work\.py"
    r"|\btee\b[^|;]*work\.py"
    r"|\b(mv|cp|install)\b[^|;]*\s\S*work\.py\s*($|[;&|\"'])"
    r"|write_text|writelines|\.write\("
    r"|open\([^)]*['\"][wa]\+?b?['\"]"
    r"|\bapply_patch\s*<|\|\s*apply_patch\b"
    r"|\bpatch\s+(-p\d|-i\b|<)|\bgit\s+apply\b"
    r"|\b(ed|ex|vim?|nvim)\s+(-\S+\s+)*\S*work\.py"
    r"|\bawk\b[^|;]*-i\s*inplace"
)


def stream_commands(path: Path):
    """Yield ("shell", command) and ("tool", name) for every action in a Claude or Codex run log."""
    for line in path.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "command_execution":
            yield "shell", item.get("command") or ""
        elif ev.get("type") == "item.completed" and item.get("type") == "file_change":
            if any(Path(c.get("path") or "").name == "work.py" for c in item.get("changes") or []):
                yield "tool", "apply_patch"
        elif ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    if b["name"] == "Bash":
                        yield "shell", b["input"].get("command", "")
                    elif b["name"] in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        yield "tool", b["name"]


# A neovain call whose output the agent never sees: sent to /dev/null, or piped into a filter.
# Redirecting only stderr (2>/dev/null) does not count.
OUTPUT_DISCARDED = re.compile(
    r"(?<![0-9&])>\s*/dev/null"
    r"|&>\s*/dev/null"
    r"|1>\s*/dev/null"
    r"|\|\s*(grep|egrep|rg|tail|head|wc|sed|awk|cut)\b"
)


def discarded_calls(path: Path) -> int:
    """How many neovain calls in a run log threw their output away or filtered it."""
    count = 0
    for kind, text in stream_commands(path):
        if kind != "shell":
            continue
        call = NEOVAIN_CALL.search(text)
        if call and "--help" not in text and OUTPUT_DISCARDED.search(text[call.start():]):
            count += 1
    return count


def scan_violations(path: Path, arm: str) -> list[str]:
    """Every edit made outside the arm's allowed tool, as short descriptions.

    In the edit arm only the agent's own file-editing tool may change work.py, and the shell may
    only read. In a neovain arm only neovain calls may change it.
    """
    found = []
    for kind, text in stream_commands(path):
        if kind == "tool":
            if arm != "edit":
                found.append(f"{text} used in a neovain arm")
        elif NEOVAIN_CALL.search(text):
            if arm == "edit":
                found.append("neovain used in the edit arm")
        elif "work.py" in text or "apply_patch" in text:
            match = SHELL_WRITE.search(text)
            if match:
                found.append(f"shell write: {match.group(0).strip()[:24]}")
    return found


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


LAYOUT_REASONS = ("blank-line layout", "trailing newline")


def code_ok(passed: bool, check: str) -> bool:
    """True if the code is right even though the layout may not be.

    The checkers fail a run for blank lines alone. A large-task failure that says only "layout
    differs" has the expected syntax tree; a small-task failure is layout-only when every reason
    is a blank-line or trailing-newline reason.
    """
    if passed:
        return True
    reasons = check.removeprefix("FAIL: ").split("; ")
    return bool(check) and all(r.startswith("layout differs") or r in LAYOUT_REASONS for r in reasons)


def run_one(a, task: str, model: str, arm: str, kb: int, rep: int, background: dict) -> dict:
    d = a.out / f"{task}_{model}_{arm}_ctx{kb}_{rep}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copy(HERE / "tasks" / task / "fixture.py", d / "work.py")
    shutil.copy(HERE / "tasks" / task / "TASKS.md", d / "TASKS.md")
    if a.agent == "codex":
        effort = ["-c", f'model_reasoning_effort="{a.effort}"'] if a.effort else []
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-m", model,
               *effort, "-"]
        prompt = background[kb] + codex_prompts(a.bin)[arm]
    else:
        allowed, denied = TOOLS[arm]
        effort = ["--effort", a.effort] if a.effort else []
        cmd = ["claude", "-p", "--model", model, *effort, "--output-format", "stream-json", "--verbose",
               "--allowedTools", *allowed, "--disallowedTools", *denied, "--max-turns", "40"]
        prompt = background[kb] + prompts(a.bin)[arm]
    env = {**os.environ, "NEOVAIN_EX_ONLY": "1" if arm == "neovain-ex" else "0"}
    t0 = time.monotonic()
    with open(d / "stream.jsonl", "w") as f:
        proc = subprocess.run(cmd, cwd=d, input=prompt, stdout=f, stderr=subprocess.PIPE, text=True,
                              timeout=1200, env=env)
    wall = time.monotonic() - t0
    check = subprocess.run(["python3", str(HERE / "tasks" / task / "check.py"), str(d / "work.py")], capture_output=True, text=True)
    row = {"task": task, "model": model, "arm": arm, "ctx_kb": kb, "rep": rep, "wall_s": round(wall, 1),
           "pass": check.returncode == 0, "check": check.stdout.strip(), "exit": proc.returncode}
    stream = d / "stream.jsonl"
    row.update(parse_codex_stream(stream, arm) if a.agent == "codex" else parse_stream(stream, arm))
    row["code_ok"] = code_ok(row["pass"], row["check"])
    row["agent"] = a.agent
    # "default" means the CLI chose: Codex models each have their own default level.
    row["effort"] = a.effort or "default"
    row["model_id"] = row["model_id"] or model
    if proc.returncode != 0:
        row["stderr"] = proc.stderr[-500:]
    return row


def parse_stream(path: Path, arm: str) -> dict:
    tool_counts, edit_calls, edit_failed = {}, 0, 0
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
        "edits_discarded": discarded_calls(path),
        "violations": len(scan_violations(path, arm)),
        "subtype": result.get("subtype"),
        "model_id": ",".join(sorted(result.get("modelUsage") or {})),
        "api_error": result.get("result") if result.get("is_error") or not result else None,
    }


def codex_prompts(binary: str) -> dict:
    common = COMMON.replace("(Read, Grep, Glob, cat -n, rg)", "(cat -n, rg, sed -n)")
    tool = f"""You must modify work.py ONLY by running the neovain CLI in the shell:
  {binary} work.py STEP...
Read {README} first to learn it. Do not write work.py any other way
(no apply_patch or other file-editing tool, no sed -i, no redirection, no scripts)."""
    return {
        "edit": f"""{common}
You must modify work.py ONLY with your built-in file-editing tool (apply_patch). Do not use shell
commands, redirection or scripts to change the file.""",
        "neovain": f"{common}\n{tool}",
        "neovain-ex": f"""{common}
{tool}
Ex-only mode is enabled: only @anchor and :ex steps are accepted. Normal-mode keys and :normal are
rejected. Insert text with :a, :i or :c.""",
    }


def parse_codex_stream(path: Path, arm: str) -> dict:
    """Metrics from `codex exec --json`. Codex reports neither cost nor the number of model requests.

    Codex has no flags to switch tools off, so the arms are enforced by the prompt alone, and
    scan_violations finds the runs that edited work.py the wrong way.
    """
    counts, edit_calls, edit_failed = {}, 0, 0
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    turns, error = 0, None
    for line in path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, item = ev.get("type"), ev.get("item") or {}
        if kind == "turn.completed":
            turns += 1
            for k in usage:
                usage[k] += (ev.get("usage") or {}).get(k) or 0
        elif kind in ("turn.failed", "error"):
            error = json.dumps(ev.get("error") or ev.get("message") or ev)[:300]
        elif kind == "item.completed" and item.get("type") == "command_execution":
            counts["shell"] = counts.get("shell", 0) + 1
            if NEOVAIN_CALL.search(item.get("command") or ""):
                edit_calls += 1
                if "FAILED at step" in (item.get("aggregated_output") or "") or item.get("exit_code") not in (0, None):
                    edit_failed += 1
        elif kind == "item.completed" and item.get("type") == "file_change":
            counts["apply_patch"] = counts.get("apply_patch", 0) + 1
            if arm == "edit":
                edit_calls += 1
                edit_failed += item.get("status") != "completed"
    if error is None and turns == 0:
        error = "codex finished no turn"
    return {
        "out_tok": usage["output_tokens"],
        "think_tok": usage["reasoning_output_tokens"],
        "cache_read_tok": usage["cached_input_tokens"],
        "cache_write_tok": None,
        "cost_usd": None,
        "turns": turns,
        "api_requests": None,
        "api_s": None,
        "tool_calls": sum(counts.values()),
        "tools": counts,
        "edit_calls": edit_calls,
        "edit_failed": edit_failed,
        "edits_discarded": discarded_calls(path),
        "violations": len(scan_violations(path, arm)),
        "subtype": None,
        "model_id": None,
        "api_error": error,
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
        groups.setdefault((r.get("task", "small"), r["model"], r.get("ctx_kb", 0), r["arm"]), []).append(r)
    cols = ["task", "model", "ctx_kb", "arm", "n", "pass", "code_ok", "out_tok", "think_tok", "turns", "api_requests", "tool_calls", "edit_calls",
            "edit_failed", "violations", "cache_read_tok", "wall_s", "cost_usd"]
    table = []
    for (task, model, kb, arm), rs in sorted(groups.items()):
        table.append({"task": task, "model": model, "ctx_kb": str(kb), "arm": arm, "n": str(len(rs)),
                      "pass": f"{sum(r['pass'] for r in rs)}/{len(rs)}",
                      "code_ok": f"{sum(code_ok(r['pass'], r['check']) for r in rs)}/{len(rs)}",
                      **{c: ms([r.get(c) for r in rs]) for c in cols[7:]}})
    w = {c: max(len(c), *(len(t[c]) for t in table)) for c in cols}
    print("  ".join(c.ljust(w[c]) for c in cols))
    for t in table:
        print("  ".join(t[c].ljust(w[c]) for c in cols))


def preflight(binary: str, agent: str) -> None:
    """Fail fast if the agent can't reach its API or neovain isn't runnable, instead of recording bogus FAILs."""
    if not shutil.which(binary):
        raise SystemExit(f"preflight: {binary!r} not found on PATH (cargo install --path .. or pass --bin)")
    if agent == "codex":
        p = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=60)
        if p.returncode != 0 or "Logged in" not in p.stdout + p.stderr:
            raise SystemExit(f"preflight: codex is not logged in:\n{p.stdout}{p.stderr}\nRun `codex login`, then retry.")
        return
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
    ap.add_argument("--agent", default="claude", choices=["claude", "codex"], help="which CLI runs the task")
    ap.add_argument("--models", default="opus,sonnet")
    ap.add_argument("--effort", default="", help="reasoning effort to request, e.g. low, medium, high "
                    "(default: leave it to the CLI, which for Codex differs per model)")
    ap.add_argument("--arms", default="edit,neovain,neovain-ex")
    ap.add_argument("--context-kb", default="0", help="comma-separated preload sizes in KB, e.g. 0,400")
    ap.add_argument("--task", default="small", help="task set(s) under tasks/, comma-separated: small,large")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("-j", "--jobs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=HERE / "runs")
    ap.add_argument("--bin", default="neovain", help="neovain binary name or path")
    ap.add_argument("--summarize-only", action="store_true")
    a = ap.parse_args()
    results = a.out / "results.jsonl"
    if not a.summarize_only:
        preflight(a.bin, a.agent)
        a.out.mkdir(parents=True, exist_ok=True)
        sizes = [int(k) for k in a.context_kb.split(",")]
        background = {kb: preload(kb) for kb in sizes}
        jobs = [(t, m, arm, kb, r) for r in range(1, a.reps + 1) for t in a.task.split(",") for kb in sizes
                for m in a.models.split(",") for arm in a.arms.split(",")]
        with ThreadPoolExecutor(a.jobs) as ex, open(results, "a") as f:
            for row in ex.map(lambda j: run_one(a, *j, background), jobs):
                f.write(json.dumps(row) + "\n")
                f.flush()
                tag = f"{row['task']:5} {row['model']:13} ctx{row['ctx_kb']:<4} {row['arm']:10} #{row['rep']}"
                if row["api_error"]:
                    print(f"{tag}  ERROR  {row['api_error']}", flush=True)
                    continue
                cost = "cost n/a" if row["cost_usd"] is None else f"${row['cost_usd']:.3f}"
                print(f"{tag}  {'PASS' if row['pass'] else 'FAIL'}  out={row['out_tok']} "
                      f"tools={row['tool_calls']} edits={row['edit_calls']} failed={row['edit_failed']} "
                      f"violations={row['violations']} wall={row['wall_s']}s {cost}", flush=True)
    summarize([json.loads(l) for l in results.read_text().splitlines() if l.strip()])


if __name__ == "__main__":
    main()
