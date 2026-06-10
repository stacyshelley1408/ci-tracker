import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"
EVENTS_FILE = DATA_DIR / "events.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def _load_events() -> list[dict]:
    if EVENTS_FILE.exists():
        return json.loads(EVENTS_FILE.read_text())
    return []


def _save_events(events: list[dict]):
    _ensure_data_dir()
    EVENTS_FILE.write_text(json.dumps(events, indent=2))


def save_event(event: dict) -> dict:
    """
    Persists a classified event to the events log.
    Adds timestamp, run_id, and a stable event_id.
    Returns the event with those fields added.
    """
    events = _load_events()

    enriched = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "alerted": False,
        "in_digest": False,
        **event,
    }

    events.append(enriched)
    _save_events(events)
    return enriched


def mark_alerted(event_id: str):
    events = _load_events()
    for e in events:
        if e.get("event_id") == event_id:
            e["alerted"] = True
            break
    _save_events(events)


def mark_in_digest(event_id: str):
    events = _load_events()
    for e in events:
        if e.get("event_id") == event_id:
            e["in_digest"] = True
            break
    _save_events(events)


def get_alertable_events() -> list[dict]:
    """Returns all unalerted daily-alert events. Threshold filtering is the caller's responsibility."""
    events = _load_events()
    return [
        e for e in events
        if not e.get("alerted")
        and e.get("alert") == "daily"
    ]


def get_digest_events(days: int = 7) -> list[dict]:
    """
    Returns events from the past N days not yet included in a digest,
    sorted by significance score descending.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = _load_events()

    recent = [
        e for e in events
        if not e.get("in_digest")
        and datetime.fromisoformat(e["timestamp"]) >= cutoff
    ]

    return sorted(recent, key=lambda e: e.get("haiku_score", 0), reverse=True)


def get_highlights(days: int = 7, min_score: int = 4) -> list[dict]:
    """
    Returns top events by score for the digest highlights section.
    """
    events = get_digest_events(days=days)
    return [e for e in events if e.get("haiku_score", 0) >= min_score]
