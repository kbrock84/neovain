# Editing files with headless Neovim (for agents)

Neovim applies Vim commands to a file without a UI:

```
nvim --clean --headless -n FILE -c 'COMMAND' -c 'COMMAND' -c 'wq'
```

`--clean` skips config and plugins, `-n` skips the swap file. Each `-c` runs one ex command, in
order, on the file, with the cursor starting on line 1. `wq` writes the file and quits.

## How it behaves

- **An error does not stop the commands after it, and the exit code is 0 either way.** Errors
  go to stderr (`E486: Pattern not found: ...`). Read them: a later command may have run on the
  wrong line, and `wq` writes whatever state the buffer is in.
- **If nothing quits, Neovim keeps running** and the command never returns. `-c 'wq'` or
  `-c 'q!'` must be the last command. A `|` chain stops at its first error, so a `wq` at the
  end of a chain does not run when an earlier part fails. To write only when every command
  succeeded: `-c 'try | CMD | CMD | wq | catch | cq | endtry'` (`cq` quits with exit code 1
  without writing).
- **At most 10 `-c` commands.** For more, put one command per line in a file and run it with
  `nvim --clean --headless -n FILE -S script.vim` (end the script with `wq`).
- `nvim --clean -es -n FILE -c ...` is silent Ex mode: it prints nothing, exits when the
  commands are done even without `q`, and exits 1 if any command failed (a later `wq` still
  writes). Its cursor starts on the **last** line, not the first.
- Nothing prints what changed. To see it, keep a copy and diff:
  `cp FILE /tmp/before && nvim ... && diff /tmp/before FILE`.
- A search wraps around the end of the file, and `/pattern` goes to the first match after the
  cursor even when the pattern matches several lines.
- Line endings (LF or CRLF) are kept. A file without a final newline gets one.

## Using it well

1. **Read first** (`rg -n`, `cat -n`) so you know the text you're aiming at.
2. **Plan the whole change, then send it as one call.** Commands run in order on the same
   buffer, so a later command sees what earlier ones did. Ten commands in one call cost one
   round trip; ten calls cost ten.
3. **Go to lines by content** (`-c '/^def foo/'`), not by number. Line numbers shift as soon as
   an earlier command adds or removes a line. Make the pattern specific enough to match one
   line; check with `rg -n` if unsure.
4. **Use ex commands for structural and bulk work.** A range can end at a pattern:
   `:.,/^class Next/-1d` deletes from the cursor line to the line before `class Next`.
   `:m` moves a range, `:t` copies it, `>` indents it, `:%s` and `:g` change every match.
5. **A block owns the blank lines above it.** Select a block together with the blank lines
   above it, and stop at its last line of code. Where two blank lines separate top-level
   blocks:

   ```
   -c '/^class Report' -c '-2,/^\S/-3d'                  delete the class
   -c '/^class Report' -c '-2,/^\S/-3m$'                 move it to the end of the file
   -c '/^class Report' -c '-2,/^\S/-3m?^class Cache?-3'  move it above class Cache
   ```

   `-2` starts two lines above the cursor, on the block's blank lines. `/^\S/-3` ends three
   lines before the next top-level line, on the block's last line of code. A block selected
   this way takes its separator along and leaves none behind. Match the numbers to the file's
   spacing: with one blank line between methods, use `-1` and `/^    def /-2`. For the last
   block in a file there is no next line to search for, so end the range with `$`.
6. **Normal-mode keys** go through `:execute`: `-c 'exe "normal! ggciwname\<Esc>"'`. Use
   `normal!` so no mapping interferes.
7. **Check the result.** A wrong edit that is still valid Vim does not fail. Diff against a
   copy, or `cat -n` the region you changed. To repair spacing, `:%s/\n\{4,}/\r\r\r/e` cuts
   runs of three or more blank lines down to two, and `:%s/\n\+\%$//e` removes blank lines at
   the end of the file.
8. **Quote each command in single quotes.** Backslashes inside single quotes reach Neovim as
   written, so write `\<word\>` once, not doubled.
