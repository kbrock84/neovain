<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="site/logo-dark.png">
    <img src="site/logo-light.png" alt="neovain" width="300">
  </picture>
</p>

# neovain

The **Neov**im **A**gent **In**terface.

Transactional vim editing for AI agents. One call applies a sequence of vim keystrokes and
ex commands to a file using headless Neovim. If every step succeeds, the file is written and
a unified diff is printed, or a summary of the changes if the diff is long. If any step fails,
nothing is written.

```
neovain [OPTIONS] FILE STEP [STEP ...]
```

| Step          | Meaning                                                                          |
|---------------|----------------------------------------------------------------------------------|
| `@regex`      | Move the cursor to the **one** line matching `regex` (vim regex). Fails if 0 or more than 1 lines match. |
| `@N@regex`    | Move the cursor to the Nth matching line.                                        |
| `:excmd`      | Ex command: `:%s/old/new/g`, `:g/pat/d`, `:10,20m$`, `:call append(line('.'), ['x'])` |
| anything else | Normal-mode keys, with `<Esc>`, `<CR>`, `<C-v>` and `<Tab>` notation.            |

Options: `-n/--dry-run` (show the changes, don't write), `--diff auto|full|summary` (what to
print, see [Output](#output)), `-C N` (diff context lines), `--sw N` (shiftwidth, default 4),
`--timeout SECS`. Put `--` before steps that look like flags.
Exit status: `0` ok, `1` a step failed (file unchanged), `2` usage error.

## Example

```console
$ neovain app.py '@^def load' 'wciwread_file<Esc>' ':%s/\<load(/read_file(/g'
--- app.py
+++ app.py
@@ -1,3 +1,3 @@
-def load(path):
+def read_file(path):
     data = open(path).read()
     return data
@@ -7,4 +7,4 @@
 
 def main():
-    d = load("in.txt")
+    d = read_file("in.txt")
     save("out.txt", d)

$ neovain app.py '@open(' 'dd'
FAILED at step 1 "@open(": anchor matched 2 lines (2,6): make the pattern more specific or use @N@
cursor was on line 1; file unchanged
```

## Output

A diff that changes at most 60 lines, and is at most 80 lines long with two lines of context,
is printed in full. For a longer one neovain prints a summary of at most 80 lines, because a
2,000-line diff goes unread. `--diff full` always prints the diff, `--diff summary` always
prints the summary, and both work with `--dry-run`. `-C` sets the context of the diff that is
printed. It has no part in the choice between the two.

This is the summary of six structural changes to a 2,350-line file, made in one call. Four of
its ten blocks are shown:

```console
$ neovain work.py ':%s/\<log_event\>/emit_event/g' ':g/^def debug_/-2,/^\S/-3d' ...
work.py: +757 -1237 lines in 60 hunks; 2354 -> 1874 lines
summary (--diff full prints the diff). -N: old line. +N: new line. N: line next to the block, in the new file.
replaced on 155 lines: log_event -> emit_event (none left)
deleted 398 lines: 961-1358  (class LegacyExporter:)
   921:    return total
  -961:class LegacyExporter:
  -962:    """Deprecated: CSV export for the v1 CLI."""
   ...
  -1357:        self.log_event("parse.score", count=len(tokens))
  -1358:        return total
   924:class EventStore:
inserted 1 line: 938  (with self._lock:)
   937:        """Synchronize every pending change to the backend."""
  +938:        with self._lock:
   939:            total = 0
reindented 120 lines: 1375-1494 -> 939-1058, indent +4 spaces  (total = 0)
   938:        with self._lock:
  +939:            total = 0
  +940:            total = sum(x.get("owner", 0) for x in tokens)
   ...
  +1057:                events = []
  +1058:            return total
   1060:    def sample_records(self, files):
moved 401 lines: 1697-2097 -> 1474-1874  (class ReportBuilder:)
  down past 211 lines: 1261-1471  (def rank_users(sessions, tokens):)
   1471:    return total
  +1474:class ReportBuilder:
  +1475:    """Builds periodic reports from the event store."""
   ...
  +1873:            jobs.pop()
  +1874:        return total
   (end of file)
```

| Line                                        | Meaning                                                         |
|---------------------------------------------|-----------------------------------------------------------------|
| `moved N lines: A-B -> C-D`                 | Lines A-B of the old file are lines C-D of the new file. The line below says what the block passed on its way. |
| `deleted N lines: A-B`                      | Lines A-B of the old file are gone.                             |
| `inserted N lines: C-D`                     | Lines C-D of the new file are new.                              |
| `changed N lines to M: A-B -> C-D`          | Lines A-B were replaced by other text, now on lines C-D.        |
| `reindented N lines: A-B -> C-D, indent +4 spaces` | The same text with other leading whitespace.             |
| `replaced on N lines: old -> new`           | The same token replacement on N lines, and how many lines still contain `old`. The five most frequent ones are named and the others counted. |
| `line endings: CRLF -> LF on N lines`       | The same lines with other line endings.                         |
| `whitespace-only lines changed: N, from +C` | Blank lines with other whitespace in them. C is the first one.  |
| `trailing whitespace changed on N lines, from +C` | The same text with other whitespace at the end of the line. |
| `spacing: 1 blank line at 12, was 2`        | A run of blank lines has another length, and that may be meant. |
| `WARNING: ...`                              | Something that is very likely wrong. See below.                 |

A block starts and ends on a line that is not blank. Its first line follows in parentheses,
then an excerpt: the line before the block, its first and last lines, and the line after it.
A moved or deleted block that holds more than one unit says so, as in
`(def load(path):) +2 more at this indent` for three functions, and the excerpt shows where
the others start. A moved or re-indented block that holds more or fewer blank lines than
before gives both sizes, as in `reindented 9 lines to 7`. When the summary would get too
long, the excerpts get shorter, and then blocks are left out and counted.

The warnings are about the two mistakes that a valid command makes most often:

```
WARNING: file ends with 3 newlines, was 1 (2 blank lines at the end)
WARNING: no blank line between 1471 and 1472, was 2
WARNING: 4 blank lines at 147-150, was 2
reindented 320 lines: 1375-1694 -> 939-1258, indent +4 spaces  (total = 0)
  WARNING: 8 lines are indented less than the block's first line, from +1060
```

The first three say that blank lines went to the wrong place. Where two blocks were joined,
the blank lines between them are compared with the blank lines each block had next to it
before. The last one says that a range ran past the end of the block it started in: a method
body that is indented together with the methods after it holds lines indented less than its
first line, and the first line of the method did not move with them.

A warning is given only where the old file shows what the spacing should be. Next to text
that is new it does not, so a run of blank lines that changed there is a `spacing:` line at
most. A line on its own that moved is treated the same way.

Warnings are not kept for the summary. After a diff that is printed in full they follow it,
below an empty line. A diff with nothing to warn about is followed by nothing.

```console
$ neovain app.py '@^def save' ':.,/^def main/-1m$'
--- app.py
+++ app.py
@@ -3,8 +3,8 @@
     return data
 
+def main():
+    d = load("in.txt")
+    save("out.txt", d)
 def save(path, data):
     open(path, "w").write(data)
 
-def main():
-    d = load("in.txt")
-    save("out.txt", d)

WARNING: file ends with 2 newlines, was 1 (1 blank line at the end)
WARNING: no blank line between 7 and 8, was 1
```

The diff behind a summary and the analysis of the change have two seconds each, and less if
the run has taken as long as `--timeout` by then. If the time runs out, the first line says
`about +N -M lines, counted roughly`, a line that starts with `note:` says what is missing,
and what was found by then is printed. The diff for `--diff full` takes the time it needs.

The summary works on lines and knows nothing about the language of the file.

## Guarantees

- **Transactional.** The first failing step aborts the run: a search miss, an ambiguous or
  missing anchor, or an ex error. The file is left untouched and the output names the
  failing step.
- **Literal typing.** Autoindent, formatoptions, textwidth and filetype plugins are all off.
  Text typed in insert mode lands exactly as typed, so write the indentation yourself.
- **No wraparound.** `wrapscan` is off, so `/pat<CR>` only searches forward from the cursor.
- **Indentation.** `>` and `<` use tabs if the file already indents with tabs. Otherwise they
  use `--sw` spaces.
- **Line endings.** CRLF/LF and a missing final newline are preserved. The file is written
  atomically and keeps its permissions.
- **No-effect warning.** A normal-mode step that changes neither the buffer nor the cursor
  prints a warning. That usually means an incomplete command.

## Using it well (for agents)

1. **Read first** (`rg -n`, `cat -n`) so you know the text you're anchoring on.
2. **Plan the whole change, then send it as one call.** Steps run in order on the same buffer,
   so a later anchor sees what earlier steps did. Ten steps in one call cost one round trip;
   ten calls cost ten. A long chain is safe: if any step fails, nothing is written.
3. **Anchor by content** (`@^def foo`), not by line number. Line numbers shift as soon as an
   earlier step adds or removes a line.
4. **Use ex commands for structural and bulk work.** A range can end at a pattern:
   `:.,/^class Next/-1d` deletes from the cursor line to the line before `class Next`.
   `:m` moves a range, `:t` copies it, `>` indents it, `:%s` and `:g` change every match.
5. **A block owns the blank lines above it.** Select a block together with the blank lines
   above it, and stop at its last line of code. Where two blank lines separate top-level
   blocks:

   ```
   '@^class Report' ':-2,/^\S/-3d'                  delete the class
   '@^class Report' ':-2,/^\S/-3m$'                 move it to the end of the file
   '@^class Report' ':-2,/^\S/-3m?^class Cache?-3'  move it above class Cache
   ```

   `-2` starts two lines above the anchor, on the block's blank lines. `/^\S/-3` ends three
   lines before the next top-level line, on the block's last line of code. A block selected
   this way takes its separator along and leaves none behind, so nothing piles up where it
   was, and a block moved to the end leaves no blank lines at the end of the file. Match the
   numbers to the file's spacing: with one blank line between methods, use `-1` and
   `/^    def /-2`. For the last block in a file there is no next line to search for, so end
   the range with `$`.
6. **Preview, then run.** Add `--dry-run` to see the changes, then send the same command
   without it. `--dry-run --diff full` shows every changed line of a long diff.
7. **Read the output.** A wrong edit that is still valid vim does not fail. After a large
   change, neovain prints a [summary](#output) in place of the diff. It names every block
   that was moved, deleted or re-indented, with its line range, its size and its first line.
   Compare them with what you meant: a block much larger than you expected is a range that ran
   too far. Act on every line that starts with `WARNING`, in a summary or below a diff. It
   says that blank lines were lost or piled up where blocks were joined, that the file ends
   in blank lines, or that a block ran past the end of the block it started in. To repair
   spacing, `:%s/\n\{4,}/\r\r\r/e` cuts runs of three or more blank lines down to two, and
   `:%s/\n\+\%$//e` removes blank lines at the end of the file.
8. **Quote each step in single quotes.** Backslashes inside single quotes reach neovain as
   written, so write `\<word\>` once, not doubled.

## Inserting literal text

Normal-mode steps go through Neovim's key notation, so `<Tab>`, `<Del>` or `<Home>` inside
typed text become keys, and so do HTML tags like `<del>`. For literal text, use the ex
commands `:a` (append after the cursor line), `:i` (insert before it) or `:c` (replace a
range). Put the text on the following lines and finish with a line containing only `.`:

```console
$ neovain page.html '@</main>' $':i\n  <p>New <del>old</del> text</p>\n.'
$ neovain page.html '@<h1>' $':c\n  <h1>New title</h1>\n.'
$ neovain new.html $':0a\n<!doctype html>\n<title>New page</title>\n.'
```

`:0a` writes into an empty file. New and empty files always get LF line endings.

## Ex-only mode

With `NEOVAIN_EX_ONLY=1`, only `@anchor` and `:ex` steps are accepted. Normal-mode keys,
`:normal` and `:execute` are rejected. Insert text with `:a`, `:i` or `:c` (see above).

## Install

neovain needs [Neovim](https://neovim.io) 0.9 or newer on `PATH`, or `NEOVAIN_NVIM` pointing at one.

On Linux and macOS:

```
curl -fsSL https://raw.githubusercontent.com/kbrock84/neovain/main/install.sh | sh
```

On Windows, in PowerShell:

```
irm https://raw.githubusercontent.com/kbrock84/neovain/main/install.ps1 | iex
```

The installer downloads the prebuilt binary for your machine from the
[releases page](https://github.com/kbrock84/neovain/releases) and verifies its checksum. It then
checks for Neovim 0.9 or newer. If Neovim is missing or too old, it asks before installing the
latest one. Everything goes into your user directory, so nothing needs sudo or administrator
rights.

For scripts and agents, answer the questions ahead of time: `NEOVAIN_INSTALL_NVIM=yes` (or `no`),
and on Windows `NEOVAIN_ADD_TO_PATH=yes` (or `no`). `NEOVAIN_VERSION=v0.1.0` pins a release.

Or build from source with Rust:

```
cargo install --git https://github.com/kbrock84/neovain
```

## Windows / Git Bash

MSYS rewrites arguments containing `/…` (e.g. `/pat<CR>`) into Windows paths. Run with
`MSYS_NO_PATHCONV=1`. neovain detects the mangling and refuses to run, rather than edit the
wrong thing. Note that it also turns off
conversion of the FILE argument, so pass a relative path or a Windows path (`C:/...`),
not an MSYS path like `/tmp/x`.

## Benchmarks

See [`bench/`](bench/README.md) for the harness comparing neovain against string-replacement
editing across models, and the results so far.

## Website

[`site/`](site) is the project website, [neovain.dev](https://neovain.dev): plain HTML, CSS and
JavaScript with no build step. It is served by Cloudflare as static assets. To deploy, run
`npx wrangler deploy` from the repository root; [`wrangler.jsonc`](wrangler.jsonc) holds the settings.

The benchmark numbers, tables and charts are written into `site/index.html` itself, between
`<!-- generated:NAME -->` markers, so the page shows them without JavaScript. After the results
change, run `python3 bench/export_site.py` to regenerate them. It splices the new HTML in with
neovain. CI runs `python3 bench/export_site.py --check` and fails if the site is out of date.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
