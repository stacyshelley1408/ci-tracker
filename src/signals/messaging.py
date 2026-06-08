import hashlib
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"


def _snapshot_path(company: str, url: str) -> Path:
    key = hashlib.md5(f"{company}:{url}".encode()).hexdigest()
    return SNAPSHOTS_DIR / f"{company}_{key}.json"


def _extract_text(page) -> str:
    return page.evaluate("""() => {
        // Capture image alt text before removing images
        const altTexts = Array.from(document.querySelectorAll('img[alt]'))
            .map(img => img.alt.trim())
            .filter(alt => alt.length > 2 && alt.length < 100);

        const remove = ['script', 'style', 'noscript', 'svg', 'video', 'iframe', 'nav', 'footer'];
        remove.forEach(tag => document.querySelectorAll(tag).forEach(el => el.remove()));

        // Strip cookie/consent management UI — it injects boilerplate plus a
        // rotating per-session User ID that would diff on every single run.
        const consentSelectors = [
            '[id*="onetrust" i]', '[class*="onetrust" i]', '[id*="ot-sdk" i]',
            '#CybotCookiebotDialog', '[id*="cookiebot" i]',
            '#truste-consent-track', '[class*="truste" i]',
            '.cc-window', '[aria-label*="cookie" i]',
            '[id*="cookie" i]', '[class*="cookie" i]',
            '[id*="consent" i]', '[class*="consent" i]',
            '[class*="gdpr" i]', '[id*="gdpr" i]',
        ];
        consentSelectors.forEach(sel => {
            try { document.querySelectorAll(sel).forEach(el => el.remove()); } catch (e) {}
        });

        const bodyText = document.body.innerText.replace(/\\s+/g, ' ').trim();
        const altSection = altTexts.length ? '[logos/images: ' + altTexts.join(', ') + ']' : '';
        return (bodyText + ' ' + altSection).trim();
    }""")


def _load_snapshot(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _save_snapshot(path: Path, content: str, content_hash: str):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "content": content,
        "hash": content_hash,
    }))


# Fragments matching any of these are cookie/consent boilerplate, not signal.
# Two groups: (1) obvious consent-UI terms, (2) generic IAB/TCF cookie-category
# descriptions that contain neither "cookie" nor "consent" and so slip through
# the obvious terms — these leaked into a real OneTrust alert.
_NOISE_MARKERS = (
    # group 1 — consent UI chrome
    "cookie", "consent", "privacy preference", "manage preferences",
    "strictly necessary", "targeting cookies", "advertising partners",
    "opt-out", "accept all", "essential only", "user id:",
    "store or retrieve information on your browser",
    # group 2 — IAB cookie-category description boilerplate
    "personalized web experience", "customize the ads", "browsing interest",
    "advertising routine", "category headings", "behavioral advertising",
    "manage actions made by you", "make the site work as you expect",
    "deliver content, maintain security",
)


def _is_noise(fragment: str) -> bool:
    f = fragment.lower()
    return any(marker in f for marker in _NOISE_MARKERS)


def _segments(text: str) -> set[str]:
    # Normalize trailing punctuation/whitespace so the final sentence (which
    # keeps its period after a ". " split) doesn't read as a change every time
    # content is appended elsewhere on the page.
    segs = (s.strip().rstrip(".").strip() for s in text.split(". "))
    return {s for s in segs if s}


def _diff_text(old: str, new: str) -> str:
    old_lines = _segments(old)
    new_lines = _segments(new)
    # sorted() so the fragments shown in an alert are stable run-to-run
    # (set iteration order varies with PYTHONHASHSEED).
    added = sorted(s for s in (new_lines - old_lines) if not _is_noise(s))
    removed = sorted(s for s in (old_lines - new_lines) if not _is_noise(s))
    parts = []
    if added:
        parts.append("Added: " + " | ".join(added[:10]))
    if removed:
        parts.append("Removed: " + " | ".join(removed[:10]))
    return "\n".join(parts) if parts else ""


def check_messaging(company: str, base_url: str, pages: list[dict]) -> list[dict]:
    """
    Scrapes each page, diffs against previous snapshot.
    Returns list of change events for pages that changed.
    """
    events = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )

        for page_config in pages:
            url_path = page_config["url"]
            full_url = base_url.rstrip("/") + url_path
            alert_level = page_config.get("alert", "daily")

            try:
                page = context.new_page()
                page.goto(full_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)

                text = _extract_text(page)
                content_hash = hashlib.md5(text.encode()).hexdigest()
                snapshot_path = _snapshot_path(company, full_url)
                previous = _load_snapshot(snapshot_path)

                if previous is None:
                    # First run — save baseline, no event
                    _save_snapshot(snapshot_path, text, content_hash)
                elif previous["hash"] != content_hash:
                    diff = _diff_text(previous["content"], text)
                    _save_snapshot(snapshot_path, text, content_hash)
                    # Hash changed but every changed fragment was noise — update
                    # the baseline and move on without firing an event.
                    if diff:
                        events.append({
                            "company": company,
                            "signal_type": "messaging_diff",
                            "source": full_url,
                            "alert": alert_level,
                            "raw_diff": diff,
                            "previous_hash": previous["hash"],
                            "current_hash": content_hash,
                        })

                page.close()

            except Exception as e:
                print(f"[messaging] failed {full_url}: {e}")
                continue

        browser.close()

    return events
