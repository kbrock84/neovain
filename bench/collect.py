"""Collect benchmark batches into results.jsonl, one row per run.

  python3 collect.py            # reads batches.json, writes results.jsonl

A batch is a directory written by run.py: its results.jsonl plus one folder per run holding that
run's stream.jsonl. The run logs stay on the machine that ran them; results.jsonl is what gets
committed. Every run is parsed again from its log, so old batches are scored by the same
violation check and the same code_ok rule as new ones.
"""
import json
import sys
from pathlib import Path

import run

HERE = Path(__file__).resolve().parent
MODEL_NAMES = {
    "opus": "Claude Opus 5.5", "sonnet": "Claude Sonnet 5.5",
    "gpt-6-astra": "GPT-6 Astra", "gpt-6-sol": "GPT-6 Sol", "gpt-6-luna": "GPT-6 Luna",
    "gpt-5.6-sol": "GPT-5.6 Sol", "gpt-5.6-terra": "GPT-5.6 Terra", "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.5": "GPT-5.5",
}
# What each CLI uses when no level is set. Claude Code: its documentation. Codex: its model list.
DEFAULT_EFFORT = {
    "opus": "medium", "sonnet": "medium",
    "gpt-5.6-sol": "low", "gpt-5.6-terra": "medium", "gpt-5.6-luna": "medium",
}
KEEP = ["out_tok", "think_tok", "api_requests", "tool_calls", "edit_calls", "edit_failed", "edits_discarded", "big_calls", "cost_usd"]


def tool_version(batch: dict, arm: str):
    """Which release of the arm's tool the batch ran: neovain from "tool", the others from "tools"."""
    if arm == "edit":
        return None
    if arm in ("neovain", "neovain-ex"):
        return batch.get("tool", "0.1.0")
    return batch["tools"][arm]


def run_dir(batch_dir: Path, row: dict) -> Path:
    tail = f"{row['model']}_{row['arm']}_ctx{row.get('ctx_kb', 0)}_{row['rep']}"
    with_task = batch_dir / f"{row.get('task', 'small')}_{tail}"
    return with_task if with_task.exists() else batch_dir / tail


def main():
    batches = json.loads((HERE / "batches.json").read_text())
    out, skipped = [], []
    for b in batches:
        batch_dir = Path(b["dir"]).expanduser()
        results = batch_dir / "results.jsonl"
        if not results.exists():
            skipped.append(f"{b['batch']}: no results yet")
            continue
        rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
        for row in rows:
            if b.get("tasks") and row.get("task", "small") not in b["tasks"]:
                continue
            if row.get("api_error"):
                skipped.append(f"{b['batch']}: {row['model']} {row['arm']} #{row['rep']} hit an API error")
                continue
            stream = run_dir(batch_dir, row) / "stream.jsonl"
            agent, arm = b["agent"], row["arm"]
            parsed = run.parse_codex_stream(stream, arm) if agent == "codex" else run.parse_stream(stream, arm)
            violations = run.scan_violations(stream, arm)
            # An edit-arm agent that writes a script to work out its patch, then applies the patch
            # with its own tool. Within the rules, and worth knowing when reading token counts.
            scripted = arm == "edit" and any(
                kind == "shell" and ("unified_diff" in text or "Begin Patch" in text)
                for kind, text in run.stream_commands(stream))
            explicit = row.get("effort", "default") != "default"
            out.append({
                "batch": b["batch"], "agent": agent, "model": row["model"], "model_name": MODEL_NAMES[row["model"]],
                "effort": row["effort"] if explicit else DEFAULT_EFFORT[row["model"]], "effort_set": explicit,
                "guidance": b["guidance"] if arm in ("neovain", "neovain-ex") else None,
                "tool": tool_version(b, arm),
                "task": row.get("task", "small"), "arm": arm, "ctx_kb": row.get("ctx_kb", 0), "rep": row["rep"],
                "pass": row["pass"], "code_ok": run.code_ok(row["pass"], row["check"]), "check": row["check"],
                "violations": violations, "valid": not violations, "scripted_patch": scripted,
                "wall_s": row["wall_s"], **{k: parsed[k] for k in KEEP},
            })
    text = "".join(json.dumps(r) + "\n" for r in out)
    (HERE / "results.jsonl").write_text(text, encoding="utf-8", newline="\n")
    print(f"collect: wrote {len(out)} runs from {len({r['batch'] for r in out})} batches to results.jsonl")
    print(f"  invalid (rule violations): {sum(not r['valid'] for r in out)}")
    for line in skipped:
        print("  skipped", line)


if __name__ == "__main__":
    sys.exit(main())
