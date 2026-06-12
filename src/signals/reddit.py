import hashlib
import json
import re
import time
from pathlib import Path
import feedparser
import requests

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"
REDDIT_BASE = "https://www.reddit.com"
# Use a plain browser UA — Reddit rate-limits self-identified bots more aggressively.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# --- Run-wide pacing and circuit breaker -------------------------------------
# Reddit throttles unauthenticated traffic by IP (and GitHub Actions egress IPs
# are throttled hard). These module-level globals are shared across every
# company in a single run so we pace ALL Reddit requests, not just the ones
# inside a single check_reddit() call, and stop entirely once we're clearly in
# the penalty box instead of hammering dozens more doomed queries.
_MIN_INTERVAL = 3.0  # minimum seconds between any two Reddit requests
_last_request_ts = 0.0
_rate_limited = False  # tripped after persistent 429s; skips remaining queries


def _throttle():
    global _last_request_ts
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def _snapshot_path(company: str) -> Path:
    key = hashlib.md5(f"{company}:reddit".encode()).hexdigest()
    return SNAPSHOTS_DIR / f"{company}_reddit_{key}.json"


def _load_snapshot(path: Path) -> set:
    if path.exists():
        return set(json.loads(path.read_text()))
    return set()


def _save_snapshot(path: Path, seen_ids: set):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(list(seen_ids)))


def _post_id_from_entry(entry) -> str:
    # RSS id is usually "t3_POSTID" or a full URL; normalize to the shortcode
    raw = getattr(entry, "id", "") or ""
    if raw.startswith("t3_"):
        return raw
    # Fall back to hashing the link so we still deduplicate
    link = getattr(entry, "link", "") or ""
    return hashlib.md5(link.encode()).hexdigest() if link else ""


def _post_summary(entry) -> str:
    title = getattr(entry, "title", "")
    summary = getattr(entry, "summary", "") or ""
    # Strip HTML tags from summary
    summary_text = re.sub(r"<[^>]+>", " ", summary).strip()[:300]
    link = getattr(entry, "link", "")
    return f"{title}\n{summary_text}\n{link}".strip()


def _build_query(terms: list[str]) -> str:
    # Quote each phrase and OR them into a single Reddit search query so one
    # request covers every term for a subreddit instead of one request per term.
    return " OR ".join(f'"{t}"' for t in terms if t)


def _matched_term(entry, terms: list[str], fallback: str) -> str:
    # We collapsed all terms into one OR query, so recover which term actually
    # matched by scanning the post text. Used only for event attribution.
    haystack = f"{getattr(entry, 'title', '')} {getattr(entry, 'summary', '')}".lower()
    for term in terms:
        if term and term.lower() in haystack:
            return term
    return fallback


def _search_subreddit_rss(subreddit: str, query: str) -> list:
    global _rate_limited
    if _rate_limited:
        return []

    url = f"{REDDIT_BASE}/r/{subreddit}/search.rss"
    params = {"q": query, "sort": "new", "t": "week", "restrict_sr": "1", "limit": "25"}
    backoff = 15

    for attempt in range(3):
        _throttle()
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else backoff
                wait = min(wait, 60)
                if attempt < 2:
                    print(f"[reddit] 429 r/{subreddit}; backing off {wait}s (attempt {attempt + 1}/3)")
                    time.sleep(wait)
                    backoff *= 2
                    continue
                # Persistent 429 from this IP — further Reddit calls will also
                # fail, so trip the breaker and stop for the rest of the run.
                _rate_limited = True
                print(f"[reddit] persistent rate limit; skipping all remaining Reddit queries this run")
                return []
            resp.raise_for_status()
            return feedparser.parse(resp.text).entries
        except requests.RequestException as e:
            print(f"[reddit] failed r/{subreddit} query: {e}")
            return []
    return []


def check_reddit(
    company: str,
    subreddits: list[str],
    search_terms: list[str],
    company_name: str,
    alert_level: str,
) -> list[dict]:
    """
    Searches subreddits for company mentions via RSS (avoids JSON API blocks).
    All search terms for a subreddit are combined into a single OR query to keep
    request volume low and avoid Reddit's rate limiter. Returns events for new
    threads not seen in the previous snapshot.
    """
    events = []
    snapshot_path = _snapshot_path(company)
    seen_ids = _load_snapshot(snapshot_path)
    new_seen_ids = set(seen_ids)

    all_terms = [company_name] + [t for t in search_terms if t != company_name]
    query = _build_query(all_terms)
    if not query:
        return events

    for subreddit in subreddits:
        entries = _search_subreddit_rss(subreddit, query)

        for entry in entries:
            post_id = _post_id_from_entry(entry)
            if not post_id or post_id in seen_ids:
                continue

            new_seen_ids.add(post_id)
            link = getattr(entry, "link", "")

            events.append({
                "company": company,
                "signal_type": "reddit",
                "source": link,
                "subreddit": subreddit,
                "search_term": _matched_term(entry, all_terms, company_name),
                "alert": alert_level,
                "raw_diff": _post_summary(entry),
                "title": getattr(entry, "title", ""),
            })

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
