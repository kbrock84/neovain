"""Small helpers shared by the rest of the package."""
import logging
import re
from datetime import datetime

log = logging.getLogger(__name__)


def chunked(items, size):
    """Yield lists of at most `size` items."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def slugify(text):
    """Lower-case text with every run of other characters turned into a dash."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def debug_dump(*values):
    """Log values while debugging."""
    for value in values:
        log.debug("dump %r", value)


def normalize(value):
    """Strip and lower-case strings; leave other values alone."""
    if isinstance(value, str):
        return value.strip().lower()
    return value


def timestamp():
    """The current time as a compact string."""
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S")
