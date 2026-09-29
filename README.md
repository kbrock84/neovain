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

1. Read first (`rg -n`, `cat -n`) so you know the text you're anchoring on.
2. Start each edit with a content anchor (`@^def foo`), not a line number or relative motion.
3. Prefer ex commands for bulk or structural work: `:%s/\<old\>/new/g`, `:g/# DEBUG$/d`,
   `:m`, `:t`.
4. **Read the diff.** Motions like `d}`, `3j` and `O` vs `o` are easy to get subtly wrong,
   and a wrong edit that is still valid vim does not fail.
5. Use `--dry-run` when unsure.

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

Requires [Neovim](https://neovim.io) 0.9+ on `PATH` (tested with 0.11), or set `NEOVAIN_NVIM`.

```
cargo install --git https://github.com/kbrock84/neovain
```

## Windows / Git Bash

MSYS rewrites arguments containing `/…` (e.g. `/pat<CR>`) into Windows paths. Run with
`MSYS_NO_PATHCONV=1`. neovain detects the mangling and refuses to run, rather than edit the
wrong thing.

## Benchmarks

See [`bench/`](bench/README.md) for the harness comparing neovain against string-replacement
editing across models, and the results so far.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
