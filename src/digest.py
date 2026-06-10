import html
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from email_utils import CATEGORY_LABELS, send_smtp


def _safe_url(url: str) -> str:
    """Return url only if it's a safe http/https link; empty string otherwise."""
    return url if url.startswith(("http://", "https://")) else ""

# Design system tokens (email-safe — inline only, no external fonts)
SLATE = "#0d1117"
SLATE_MID = "#1c2b35"
ACCENT = "#1a8a80"
WARM = "#00695c"
WARM_WHITE = "#f5f8f7"
SOFT_GRAY = "#eaf1f0"
TEXT_MUTED = "#6b7280"
TEXT_LIGHT = "#9ca3af"
BORDER = "#ccdeda"


def _label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.upper().replace("_", " "))


def _format_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d, %Y")
    except Exception:
        return iso


def _event_card(event: dict, show_company: bool = True) -> str:
    company = html.escape(event.get("company", ""))
    category = event.get("haiku_category", "")
    label = html.escape(_label(category))
    score = event.get("haiku_score", "")
    source = event.get("source", "")
    safe_href = _safe_url(source)
    source_display = html.escape(source[:80])
    summary = html.escape(event.get("raw_diff", "").strip()[:400])
    timestamp = html.escape(_format_date(event.get("timestamp", "")))

    company_row = ""
    if show_company:
        company_row = f"""
        <tr>
          <td style="padding:0 0 4px 0;font-family:Georgia,serif;font-size:11px;
                     letter-spacing:.12em;text-transform:uppercase;color:{TEXT_MUTED};">
            {company}
          </td>
        </tr>"""

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="margin-bottom:16px;border-left:2px solid {WARM};padding-left:12px;">
      <tr><td>{company_row}
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="font-family:Georgia,serif;font-size:10px;letter-spacing:.14em;
                       text-transform:uppercase;color:{ACCENT};padding-bottom:4px;">
              {label}
            </td>
            <td align="right" style="font-family:Arial,sans-serif;font-size:11px;
                                     color:{TEXT_LIGHT};">
              {timestamp} &nbsp;·&nbsp; {score}/5
            </td>
          </tr>
        </table>
        <p style="margin:4px 0 6px;font-family:Arial,sans-serif;font-size:13px;
                  line-height:1.5;color:{SLATE};font-weight:300;">
          {summary}
        </p>
        <a href="{safe_href}" style="font-family:Arial,sans-serif;font-size:11px;
                                  color:{ACCENT};text-decoration:none;">
          {source_display}{'…' if len(source) > 80 else ''}
        </a>
      </td></tr>
    </table>"""


def _section_header(title: str) -> str:
    return f"""
    <tr>
      <td style="padding:28px 0 12px;">
        <p style="margin:0 0 8px;font-family:Georgia,serif;font-size:11px;
                  letter-spacing:.16em;text-transform:uppercase;color:{TEXT_MUTED};">
          {title}
        </p>
        <div style="height:1px;background:{BORDER};"></div>
      </td>
    </tr>"""


def _company_header(name: str, count: int) -> str:
    name = html.escape(name)
    noun = "signal" if count == 1 else "signals"
    return f"""
    <tr>
      <td style="padding:20px 0 10px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="font-family:Georgia,serif;font-size:16px;font-weight:400;
                       color:{SLATE_MID};">
              {name}
            </td>
            <td align="right" style="font-family:Arial,sans-serif;font-size:11px;
                                     color:{TEXT_LIGHT};letter-spacing:.08em;">
              {count} {noun}
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def build_html(highlights: list[dict], all_events: list[dict], date_range: str) -> str:
    by_company = defaultdict(list)
    for e in all_events:
        by_company[e.get("company", "Unknown")].append(e)

    highlights_html = ""
    if highlights:
        cards = "".join(_event_card(e, show_company=True) for e in highlights[:5])
        highlights_html = f"""
        {_section_header("This Week's Highlights")}
        <tr><td>{cards}</td></tr>"""

    company_html = ""
    for company, events in sorted(by_company.items()):
        sorted_events = sorted(events, key=lambda e: e.get("haiku_score", 0), reverse=True)
        cards = "".join(_event_card(e, show_company=False) for e in sorted_events)
        company_html += f"""
        {_company_header(company, len(events))}
        <tr><td>{cards}</td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{WARM_WHITE};">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{WARM_WHITE};">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;">

          <!-- Header -->
          <tr>
            <td style="padding-bottom:8px;border-bottom:1px solid {BORDER};">
              <p style="margin:0 0 4px;font-family:Georgia,serif;font-size:22px;
                        font-weight:400;color:{SLATE};">
                Competitive Intelligence Digest
              </p>
              <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;
                        letter-spacing:.1em;text-transform:uppercase;color:{TEXT_MUTED};">
                {date_range}
              </p>
            </td>
          </tr>

          {highlights_html}
          {_section_header("By Company")}
          {company_html}

          <!-- Footer -->
          <tr>
            <td style="padding-top:28px;border-top:1px solid {BORDER};">
              <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;
                        color:{TEXT_LIGHT};letter-spacing:.06em;">
                CI Tracker · Generated automatically
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_digest(highlights: list[dict], all_events: list[dict]) -> list[str]:
    """
    Builds and sends the weekly HTML digest email.
    Returns list of event_ids included in the digest.
    """
    if not all_events:
        print("[digest] no events to include")
        return []

    today = datetime.now(timezone.utc)
    week_ago = today - timedelta(days=7)
    date_range = f"{week_ago.strftime('%b %d')} – {today.strftime('%b %d, %Y')}"

    html = build_html(highlights, all_events, date_range)
    subject = f"CI Digest: {date_range}"

    try:
        send_smtp(subject, html, mime_type="html")
        count = len(all_events)
        print(f"[digest] sent {count} event(s) to {os.environ.get('ALERT_EMAIL')}")
        return [e["event_id"] for e in all_events]
    except Exception as e:
        print(f"[digest] failed to send: {e}")
        return []
