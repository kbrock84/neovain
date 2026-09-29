"""Record ingestion pipeline."""
import json
import logging
import time

log = logging.getLogger(__name__)

CACHE_TTL = 3600


def load_config(path, defaults=None):
    """Load a JSON config file, merged over defaults."""
    with open(path) as f:
        cfg = json.load(f)
    merged = dict(defaults or {})
    merged.update(cfg)
    print("config loaded", merged)  # DEBUG
    return merged


def parse_record(line):
    """Parse one tab-separated record."""
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 3:
        raise ValueError(f"bad record: {line!r}")
    name, age, city = parts
    return {"name": name, "age": int(age), "city": city}


def helper_a(records):
    return [r for r in records if r["age"] >= 18]


def helper_b(records):
    return sorted(records, key=lambda r: r["name"])


def fetch(source, timeout=30):
    lines = source.read_lines(timeout=timeout)
    print("fetched", len(lines))  # DEBUG
    records = [parse_record(l) for l in lines if l.strip()]
    return records


def legacy_export(records, path):
    """Deprecated: use export() instead.

    Kept for the v1 CLI.
    """
    with open(path, "w") as f:
        for r in records:
            f.write(f"{r['name']},{r['age']},{r['city']}\n")


def export(records, path):
    with open(path, "w") as f:
        json.dump(records, f)


def is_stale(ts):
    return time.time() - ts > 3600


def run(source, out):
    records = fetch(source)
    records = helper_b(helper_a(records))
    print("exporting", len(records))  # DEBUG
    export(records, out)
    extra = parse_record("x\t1\ty")
    log.info("done, cache ttl %s, sample %s", CACHE_TTL, extra)


def reparse(lines):
    return list(map(parse_record, lines))
