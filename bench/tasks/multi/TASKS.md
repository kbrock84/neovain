Apply ALL of these changes to the Python package in app/ (13 files, about 1,900 lines). Keep
everything else unchanged and every file valid Python. Do not reformat: a line you do not need
to change stays as it is, a deleted line leaves no blank line behind, and top-level definitions
stay two blank lines apart.

1. Rename the function `fetch_rows` to `query_rows`: its definition in db.py, every import and
   every call, including calls written `db.fetch_rows(...)`. The functions `fetch_rows_cached`
   and `prefetch_rows` keep their names.
2. Rename the function `send_event` to `publish` and pass its second argument by keyword:
   `send_event(kind, x)` becomes `publish(kind, payload=x)`. Its definition in events.py becomes
   `def publish(kind, payload):`. Change every import and every call, including calls written
   `events.send_event(...)` and calls that span several lines.
3. Remove the parameter `legacy` from `connect` in db.py together with the `if legacy:` block in
   its body, and remove the `legacy=...` argument from every call.
4. Replace every `datetime.utcnow()` with `datetime.now(timezone.utc)`, and add `timezone` at the
   end of the `from datetime import ...` line of each file that uses it.
5. Delete the function `debug_dump` from utils.py, its name from every import (the whole line
   where it is the only name imported), and every statement that calls it.
