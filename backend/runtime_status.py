"""Safe, bounded operational observations; never stores prompts or credentials."""
from datetime import datetime, timezone
from threading import Lock

_lock = Lock()
_observations = {}


def record(service, *, error=None, model=None):
    with _lock:
        _observations[service] = {'state': 'unavailable' if error else 'available', 'error': error,
                                  'model': model, 'checked_at': datetime.now(timezone.utc).isoformat()}


def snapshot():
    with _lock:
        return {name: dict(_observations.get(name, {'state': 'not_checked', 'error': None, 'model': None, 'checked_at': None}))
                for name in ['generation', 'embeddings']}
