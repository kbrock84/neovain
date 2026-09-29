"""Extract the side-by-side demo for the site from one neovain run and one Edit run.

  python3 demo.py runs/large/large_opus_neovain_ctx0_1 runs/large/large_opus_edit_ctx0_1 > demo-large.json

The run logs (stream.jsonl) are not committed, so the extracted demo is.
"""
import json
import re
import sys
from pathlib import Path

NEOVAIN_CALL = re.compile(r"(?:^|[\s;&|(])neovain\s")


def tool_uses(run):
    for line in (Path(run) / "stream.jsonl").read_text().splitlines():
        event = json.loads(line)
        if event.get("type") == "assistant":
            for block in event["message"]["content"]:
                if block.get("type") == "tool_use":
                    yield block


def neovain_command(command):
    """The neovain invocation itself, without a leading `cd ...;` or anything after it (pipes, redirects, `;`)."""
    start = NEOVAIN_CALL.search(command).start()
    text = command[start:].lstrip(" ;&|(")
    return re.split(r"\s(?:\||>|;|&&)", text)[0].strip()


def lines(text):
    return text.count("\n") + 1 if text else 0


def edit_summary(inp):
    old, new = inp.get("old_string", ""), inp.get("new_string", "")
    if inp.get("replace_all"):
        summary = f"replace every {old.strip()} with {new.strip()}"
    elif not new:
        summary = f"delete {lines(old)} lines"
    else:
        summary = f"replace {lines(old)} lines with {lines(new)}"
    return {"summary": summary, "old_chars": len(old), "new_chars": len(new)}


def main():
    neo_run, edit_run = sys.argv[1], sys.argv[2]
    calls = [neovain_command(b["input"]["command"]) for b in tool_uses(neo_run)
             if b["name"] == "Bash" and NEOVAIN_CALL.search(b["input"]["command"])]
    final = [c for c in calls if not re.search(r"\s(-n|--dry-run)\s", c)][-1]
    edits = [edit_summary(b["input"]) for b in tool_uses(edit_run) if b["name"] == "Edit"]
    json.dump({
        "source": {"neovain": Path(neo_run).name, "edit": Path(edit_run).name},
        "neovain_command": final,
        "neovain_calls": len(calls),
        "neovain_chars": sum(len(c) for c in calls),
        "edit_count": len(edits),
        "edit_chars": sum(e["old_chars"] + e["new_chars"] for e in edits),
        "edit_calls": edits,
    }, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
