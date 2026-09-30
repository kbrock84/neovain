"""Event publishing: a queue that a worker drains."""
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

_QUEUE = []


def build_payload(subject, actor):
    """The standard payload for an event about a subject."""
    return {"subject": subject.get("id"), "actor": actor.get("id"), "at": datetime.utcnow().isoformat()}


def send_event(kind, payload):
    """Queue an event for delivery."""
    _QUEUE.append((kind, json.dumps(payload, default=str)))
    log.debug("queued %s", kind)
    return True


def send_many(kinds, payload):
    """Queue the same payload under several kinds."""
    for kind in kinds:
        send_event(kind, payload)
    return len(kinds)


def resend(failed):
    """Queue again the events that a worker could not deliver."""
    for kind, body in failed:
        send_event(
            kind,
            json.loads(body),
        )
    return len(failed)


def drain(limit=100):
    """Take up to `limit` queued events off the queue."""
    taken = _QUEUE[:limit]
    del _QUEUE[:limit]
    return taken
