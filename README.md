<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="site/logo-dark.png">
    <img src="site/logo-light.png" alt="neovain" width="300">
  </picture>
</p>

# neovain

Transactional vim editing for AI agents. One call applies a sequence of vim keystrokes and
ex commands to a file using headless Neovim. If every step succeeds, the file is written and
a unified diff is printed. If any step fails, nothing is written.

```
neovain [OPTIONS] FILE STEP [STEP ...]
```

| Step          | Meaning                                                                          |
|---------------|----------------------------------------------------------------------------------|
| `@regex`      | Move the cursor to the **one** line matching `regex` (vim regex). Fails if 0 or more than 1 lines match. |
| `@N@regex`    | Move the cursor to the Nth matching line.                                        |
| `:excmd`      | Ex command: `:%s/old/new/g`, `:g/pat/d`, `:10,20m$`, `:call append(line('.'), ['x'])` |
| anything else | Normal-mode keys, with `<Esc>`, `<CR>`, `<C-v>` and `<Tab>` notation.            |

Options: `-n/--dry-run` (show the diff, don't write), `-C N` (diff context lines),
`--sw N` (shiftwidth, default 4), `--timeout SECS`. Put `--` before steps that look like flags.
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
6. **Preview, then run.** Add `--dry-run` to see the diff, then send the same command without it.
7. **Check the result.** A wrong edit that is still valid vim does not fail. Read the diff, and
   after a large change confirm the structure and the spacing: `rg -n -B3 '^(class|def) '`
   shows the lines above each top-level block, and `tail -c 50 FILE | od -c` shows how the
   file ends. To repair spacing, `:%s/\n\{4,}/\r\r\r/e` cuts runs of three or more blank lines
   down to two, and `:%s/\n\+\%$//e` removes blank lines at the end of the file.
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
