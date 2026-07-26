import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"

# A monitor that fails every run is indistinguishable from a competitor that
# never changes: both produce no events. The error-page filter below made that
# worse, not better -- it correctly stopped emitting garbage diffs, and in doing
# so turned a loud failure into a silent one. ServiceNow's three monitored URLs
# erred from 2026-06-05 and nothing reported it; after the filter shipped on
# 2026-06-23 the noise stopped and five weeks of silence read as "no news".
#
# So a failed fetch now increments a per-URL counter, and crossing the threshold
# emits a monitor_health event through the normal alert path. Success resets it.
MONITOR_FAILURE_THRESHOLD = 3

# Warn on the crossing, then once a week while it stays broken. Warning every
# run would have meant 35 alerts for the ServiceNow outage, which is how a real
# signal gets filtered to trash by the person receiving it.
MONITOR_REWARN_EVERY = 7


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

        const bodyEl = document.body || document.documentElement;
        const bodyText = bodyEl ? bodyEl.innerText.replace(/\\s+/g, ' ').trim() : '';
        const altSection = altTexts.length ? '[logos/images: ' + altTexts.join(', ') + ']' : '';
        return (bodyText + ' ' + altSection).trim();
    }""")


def _load_snapshot(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _has_baseline(snapshot: dict | None) -> bool:
    """
    True only when a snapshot carries content worth diffing against.

    A snapshot can now exist with failure metadata and no content, when the very
    first fetch of a URL failed. Treating that as a baseline would diff real page
    text against an empty string on the next success and fire a false wholesale
    rewrite, so presence of the FILE is no longer the test. Presence of content is.

    It also rejects a baseline that is itself an error page. The error-page filter
    stopped NEW poisoning when it shipped, but never cleaned what was already
    stored: all three ServiceNow messaging baselines on the data branch are a
    239-char "Access Denied" body saved before the filter existed. Diffing a
    recovered page against one of those fires exactly the wholesale false shift
    the filter was built to prevent. Rejecting it here self-heals -- the next good
    fetch replaces the poison instead of diffing against it.
    """
    content = (snapshot or {}).get("content")
    if not content:
        return False
    return not _is_error_page(content)


def _save_snapshot(path: Path, content: str, content_hash: str):
    """Records a successful fetch, which also clears any failure streak."""
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "content": content,
        "hash": content_hash,
        "last_success": datetime.now(timezone.utc).isoformat(),
        "consecutive_failures": 0,
    }))


def _record_failure(path: Path, reason: str) -> int:
    """
    Increments this URL's consecutive-failure count and returns the new total.

    Deliberately preserves content and hash: the whole point of treating a bad
    fetch as a failure is that the good baseline survives it. Only the failure
    metadata is written.
    """
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    snapshot = _load_snapshot(path) or {}
    snapshot["consecutive_failures"] = snapshot.get("consecutive_failures", 0) + 1
    snapshot["last_failure"] = datetime.now(timezone.utc).isoformat()
    snapshot["last_failure_reason"] = reason
    path.write_text(json.dumps(snapshot))
    return snapshot["consecutive_failures"]


def _should_warn(failures: int) -> bool:
    """Warn on the threshold crossing, then every MONITOR_REWARN_EVERY runs after."""
    if failures < MONITOR_FAILURE_THRESHOLD:
        return False
    return failures == MONITOR_FAILURE_THRESHOLD or failures % MONITOR_REWARN_EVERY == 0


def _monitor_health_event(company: str, url: str, failures: int, reason: str, last_success: str | None) -> dict:
    """
    A monitor-health warning, shaped like any other event so it rides the existing
    alert and digest path.

    Pre-scored on purpose. This is a deterministic fact about our own collection,
    not a competitor move, so sending it to the LLM classifier would spend a call
    to have a category guessed for it and would let a hallucinated score bury it.
    Score 4 clears every configured threshold (defaults are 3 and 4).
    """
    since = f" Last good fetch: {last_success[:10]}." if last_success else " No successful fetch on record."
    return {
        "company": company,
        "signal_type": "monitor_health",
        "source": url,
        "alert": "daily",
        "raw_diff": (
            f"MONITOR DOWN: {failures} consecutive failed fetches ({reason}).{since} "
            f"No messaging signal has been collected from this URL since. "
            f"An empty change history for it means we are not looking, not that nothing changed."
        ),
        "haiku_category": "monitor_health",
        "haiku_score": 4,
        "haiku_summary": (
            f"Monitoring for {url} has failed {failures} runs in a row ({reason}). "
            f"Competitive intel from this page is stale, not quiet."
        ),
    }


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


# A monitored page sometimes comes back as a CDN/WAF error page instead of the
# real content (origin down, rate-limit, bot block). The body is short and its
# only "content" is a rotating error-reference ID, so two such fetches diff
# against each other and fire a bogus messaging shift (this surfaced as a
# ServiceNow "Reference #18.x... errors.edgesuite.net" 3/5 alert). Worse, if an
# error page overwrites a good baseline, the next real fetch diffs against it
# and fires an even larger false shift. Treat these as a failed fetch: don't
# emit, don't overwrite the snapshot.
_ERROR_PAGE_MARKERS = (
    "errors.edgesuite.net",          # Akamai reference-error page
    "reference #",                   # Akamai error-reference line
    "the request could not be satisfied",  # CloudFront
    "request blocked",               # CloudFront/WAF
    "access denied",                 # generic WAF / origin 403 page
    "you don't have permission to access",  # Apache/Akamai 403
    "attention required",            # Cloudflare challenge
    "checking your browser before",  # Cloudflare interstitial
    "ddos protection by",            # Cloudflare
    "502 bad gateway", "503 service", "504 gateway",
)


def _is_error_page(text: str) -> bool:
    # Error pages are short; real marketing pages that happen to quote one of
    # these phrases in body copy are long. Gate on length to avoid clipping a
    # legitimate page, then require an error signature.
    t = text.lower()
    if len(t) > 1500:
        return False
    return any(marker in t for marker in _ERROR_PAGE_MARKERS)


# Skip-navigation links ("Skip to Main Content", "Skip to Content") are
# accessibility UI that changes independently of page content. Strip them
# from segments before comparison so a site-wide skip-nav tweak doesn't
# fire alerts on every monitored page.
_SKIP_NAV_RE = re.compile(r'\bskip to (?:main )?content\b', re.IGNORECASE)


def _normalize_segment(s: str) -> str:
    return _SKIP_NAV_RE.sub("", s).strip()


def _segments(text: str) -> set[str]:
    # Normalize trailing punctuation/whitespace so the final sentence (which
    # keeps its period after a ". " split) doesn't read as a change every time
    # content is appended elsewhere on the page.
    segs = (s.strip().rstrip(".").strip() for s in text.split(". "))
    result = set()
    for s in segs:
        if s:
            n = _normalize_segment(s)
            if n:
                result.add(n)
    return result


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
            # Resolved before the fetch so both failure paths below can record
            # against it, including an exception raised by goto() itself.
            snapshot_path = _snapshot_path(company, full_url)

            try:
                page = context.new_page()
                page.goto(full_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)

                text = _extract_text(page)

                # A CDN/WAF error page is a failed fetch dressed as content.
                # Skip without saving so the good baseline is preserved, but
                # count it: a page that errors forever must not read as silence.
                if _is_error_page(text):
                    failures = _record_failure(snapshot_path, "CDN or WAF error page")
                    print(f"[messaging] error page for {full_url}, skipping ({failures} in a row)")
                    if _should_warn(failures):
                        events.append(_monitor_health_event(
                            company, full_url, failures, "CDN or WAF error page",
                            (_load_snapshot(snapshot_path) or {}).get("last_success"),
                        ))
                    page.close()
                    continue

                content_hash = hashlib.md5(text.encode()).hexdigest()
                previous = _load_snapshot(snapshot_path)

                if not _has_baseline(previous):
                    # First usable fetch — save baseline, no event
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
                # A timeout or navigation error is the same class of problem as an
                # error page: the monitor did not work. Count it the same way, or a
                # URL that times out every single run stays invisible.
                failures = _record_failure(snapshot_path, "fetch error")
                print(f"[messaging] failed {full_url}: {e} ({failures} in a row)")
                if _should_warn(failures):
                    events.append(_monitor_health_event(
                        company, full_url, failures, f"fetch error: {type(e).__name__}",
                        (_load_snapshot(snapshot_path) or {}).get("last_success"),
                    ))
                continue

        browser.close()

    return events
