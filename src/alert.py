import os
from datetime import datetime, timezone

from email_utils import CATEGORY_LABELS, send_smtp


def _format_event(event: dict) -> str:
    company = event.get("company", "Unknown")
    category = event.get("haiku_category", "")
    label = CATEGORY_LABELS.get(category, category.upper())
    score = event.get("haiku_score", "")
    source = event.get("source", "")
    summary = event.get("raw_diff", "").strip()

    lines = [
        f"[{label}] {company} (score: {score}/5)",
        f"Source: {source}",
        "",
        summary[:600],
        "-" * 60,
    ]
    return "\n".join(lines)


def _build_body(events: list[dict]) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    count = len(events)
    noun = "signal" if count == 1 else "signals"

    header = [
        f"CI Alert — {count} significant {noun} detected",
        f"Run time: {timestamp}",
        "=" * 60,
        "",
    ]
    return "\n".join(header) + "\n" + "\n\n".join(_format_event(e) for e in events)


def send_alert(events: list[dict]) -> list[str]:
    """
    Sends a plain-text alert email for significant events.
    Returns list of event_ids successfully alerted.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e.get("haiku_score", 0), reverse=True)
    companies = sorted(set(e.get("company", "") for e in sorted_events))
    count = len(sorted_events)
    noun = "signal" if count == 1 else "signals"
    subject = f"CI Alert: {count} {noun} — {', '.join(companies)}"

    try:
        send_smtp(subject, _build_body(sorted_events), mime_type="plain")
        print(f"[alert] sent {count} {noun} to {os.environ.get('ALERT_EMAIL')}")
        return [e["event_id"] for e in sorted_events]
    except Exception as e:
        print(f"[alert] failed to send: {e}")
        return []
