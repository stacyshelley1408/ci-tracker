import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

CATEGORY_LABELS = {
    "pricing": "PRICING CHANGE",
    "messaging": "MESSAGING SHIFT",
    "product": "PRODUCT UPDATE",
    "hiring": "HIRING SIGNAL",
    "partnership": "PARTNERSHIP",
    "funding": "FUNDING / M&A",
    "leadership": "LEADERSHIP CHANGE",
    "customer": "NEW CUSTOMER",
    "threat_intel": "THREAT INTEL",
    "community": "COMMUNITY MENTION",
    "review": "REVIEW ACTIVITY",
    "competitive_positioning": "COMPETITIVE POSITIONING",
    "press": "PRESS / ANALYST",
}


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


def _build_alert_body(events: list[dict]) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    count = len(events)
    noun = "signal" if count == 1 else "signals"

    header = [
        f"CI Alert — {count} significant {noun} detected",
        f"Run time: {timestamp}",
        "=" * 60,
        "",
    ]

    body = "\n\n".join(_format_event(e) for e in events)
    return "\n".join(header) + "\n" + body


def _send_smtp(subject: str, body: str):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    alert_email = os.environ.get("ALERT_EMAIL")

    if not all([smtp_user, smtp_password, alert_email]):
        raise ValueError("SMTP_USER, SMTP_PASSWORD, and ALERT_EMAIL must be set")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = alert_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, alert_email, msg.as_string())


def send_alert(events: list[dict]) -> list[str]:
    """
    Sends a plain-text alert email for significant events.
    Returns list of event_ids successfully alerted.
    """
    if not events:
        return []

    # Group by score descending so highest-priority items appear first
    sorted_events = sorted(events, key=lambda e: e.get("haiku_score", 0), reverse=True)

    companies = sorted(set(e.get("company", "") for e in sorted_events))
    company_str = ", ".join(companies)
    count = len(sorted_events)
    noun = "signal" if count == 1 else "signals"
    subject = f"CI Alert: {count} {noun} — {company_str}"

    body = _build_alert_body(sorted_events)

    try:
        _send_smtp(subject, body)
        print(f"[alert] sent {count} {noun} to {os.environ.get('ALERT_EMAIL')}")
        return [e["event_id"] for e in sorted_events]
    except Exception as e:
        print(f"[alert] failed to send: {e}")
        return []
