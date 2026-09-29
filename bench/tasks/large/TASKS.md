Apply ALL of these edits to work.py (about 2,350 lines). Keep everything else unchanged and the
file valid Python. Layout: exactly two blank lines between top-level definitions, one blank line
between methods (as now), and a single trailing newline.

1. Delete the class `LegacyExporter` entirely.
2. Move the class `ReportBuilder` to the very end of the file (after the last function).
3. In `EventStore.sync_all`, wrap every statement after the docstring in a `with self._lock:`
   block. The docstring stays first; the wrapped statements get 4 more spaces of indentation.
4. Swap the classes `CacheLayer` and `SessionManager`, so `SessionManager` comes first.
5. Rename the method `log_event` to `emit_event`: its definition and every call.
6. Delete every top-level function whose name starts with `debug_`.
