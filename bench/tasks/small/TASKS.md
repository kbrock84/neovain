Apply ALL of these edits to work.py. Keep the file otherwise unchanged and valid Python.

1. Rename the function `parse_record` to `parse_row` everywhere (definition and every usage).
2. Add a keyword parameter `strict=False` to `load_config`, after `defaults=None`.
3. Delete the function `legacy_export` completely (including its docstring), leaving exactly
   two blank lines between the surrounding functions.
4. Every literal `3600` must become `SECONDS_PER_HOUR`, and add the line
   `SECONDS_PER_HOUR = 3600` directly above `CACHE_TTL = ...`.
5. Wrap the entire body of `fetch` in `try:` / `except Exception:` where the except block is
   `log.exception("fetch failed")` followed by `raise`. Use 4-space indentation.
6. Delete every line ending in `# DEBUG`.
7. Move the function `helper_b` so it appears before `helper_a` (two blank lines between
   top-level functions, as elsewhere).
