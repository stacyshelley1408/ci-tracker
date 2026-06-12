import hashlib
import json
import os
import re
import time
from pathlib import Path
import feedparser
import requests

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"
REDDIT_BASE = "https://www.reddit.com"
OAUTH_BASE = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Plain browser UA for the unauthenticated RSS fallback — Reddit rate-limits
# self-identified bots more aggressively on the public endpoints.
ANON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}
# Reddit's policy requires the UA format: platform:appname:version (by /u/user).
# Non-conforming UAs are throttled harder or suspended. The username comes from
# REDDIT_USERNAME (contact info per policy); omitted gracefully if unset.
def _oauth_ua() -> str:
    user = os.environ.get("REDDIT_USERNAME", "").strip().lstrip("/").removeprefix("u/")
    contact = f" (by /u/{user})" if user else ""
    return f"python:ci-tracker:1.0{contact}"

# --- Run-wide pacing and circuit breaker -------------------------------------
# Reddit throttles unauthenticated traffic by IP (and GitHub Actions egress IPs
# are throttled hard). When OAuth creds are set, requests are metered by token
# at ~100 req/min instead, so we can pace faster. These module-level globals are
# shared across every company in a single run.
_INTERVAL_AUTH = 1.1   # ~55 req/min, comfortably under the 100/min OAuth budget
_INTERVAL_ANON = 3.0   # conservative for the unauthenticated fallback
_last_request_ts = 0.0
_rate_limited = False   # tripped after persistent 429s; skips remaining queries

# Cached app-only OAuth token.
_token = None
_token_expiry = 0.0


def _throttle(interval: float):
    global _last_request_ts
    wait = interval - (time.monotonic() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def _oauth_headers():
    """
    Return bearer-auth headers if Reddit app creds are configured, else None.
    Uses the client_credentials (app-only) grant and caches the token until it
    nears expiry. Any failure returns None so the caller falls back to the
    unauthenticated RSS path.
    """
    global _token, _token_expiry
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    if _token and time.monotonic() < _token_expiry:
        return {"Authorization": f"bearer {_token}", "User-Agent": _oauth_ua()}

    try:
        resp = requests.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": _oauth_ua()},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        _token = payload["access_token"]
        # Refresh a minute early to avoid using a token that expires mid-request.
        _token_expiry = time.monotonic() + payload.get("expires_in", 3600) - 60
        return {"Authorization": f"bearer {_token}", "User-Agent": _oauth_ua()}
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[reddit] OAuth token fetch failed, falling back to anonymous: {e}")
        return None


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


def _build_query(terms: list[str]) -> str:
    # Quote each phrase and OR them into a single Reddit search query so one
    # request covers every term for a subreddit instead of one request per term.
    return " OR ".join(f'"{t}"' for t in terms if t)


def _matched_term(item: dict, terms: list[str], fallback: str) -> str:
    # We collapsed all terms into one OR query, so recover which term actually
    # matched by scanning the post text. Used only for event attribution.
    haystack = f"{item['title']} {item['summary']}".lower()
    for term in terms:
        if term and term.lower() in haystack:
            return term
    return fallback


def _post_summary(item: dict) -> str:
    summary_text = re.sub(r"<[^>]+>", " ", item["summary"]).strip()[:300]
    return f"{item['title']}\n{summary_text}\n{item['link']}".strip()


def _normalize_rss(entries) -> list[dict]:
    items = []
    for entry in entries:
        raw_id = getattr(entry, "id", "") or ""
        link = getattr(entry, "link", "") or ""
        if raw_id.startswith("t3_"):
            post_id = raw_id
        else:
            post_id = hashlib.md5(link.encode()).hexdigest() if link else ""
        items.append({
            "id": post_id,
            "link": link,
            "title": getattr(entry, "title", "") or "",
            "summary": getattr(entry, "summary", "") or "",
        })
    return items


def _normalize_json(children) -> list[dict]:
    items = []
    for child in children:
        d = child.get("data", {})
        permalink = d.get("permalink", "")
        items.append({
            "id": d.get("name") or hashlib.md5(permalink.encode()).hexdigest(),
            "link": f"{REDDIT_BASE}{permalink}" if permalink else d.get("url", ""),
            "title": d.get("title", "") or "",
            "summary": d.get("selftext", "") or "",
        })
    return items


def _search_subreddit(subreddit: str, query: str) -> list[dict]:
    """Search a subreddit, preferring authenticated OAuth, falling back to RSS."""
    global _rate_limited
    if _rate_limited:
        return []

    headers = _oauth_headers()
    authed = headers is not None
    interval = _INTERVAL_AUTH if authed else _INTERVAL_ANON
    base = OAUTH_BASE if authed else REDDIT_BASE
    path = f"/r/{subreddit}/search" + ("" if authed else ".rss")
    params = {"q": query, "sort": "new", "t": "week", "restrict_sr": "1", "limit": "25"}
    if authed:
        params["raw_json"] = "1"
    backoff = 15

    for attempt in range(3):
        _throttle(interval)
        try:
            resp = requests.get(base + path, headers=headers or ANON_HEADERS,
                                params=params, timeout=20)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else backoff
                wait = min(wait, 60)
                if attempt < 2:
                    print(f"[reddit] 429 r/{subreddit}; backing off {wait}s (attempt {attempt + 1}/3)")
                    time.sleep(wait)
                    backoff *= 2
                    continue
                _rate_limited = True
                mode = "authenticated" if authed else "anonymous"
                print(f"[reddit] persistent rate limit ({mode}); skipping all remaining Reddit queries this run")
                return []
            resp.raise_for_status()
            if authed:
                return _normalize_json(resp.json().get("data", {}).get("children", []))
            return _normalize_rss(feedparser.parse(resp.text).entries)
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
    Searches subreddits for company mentions. Uses authenticated Reddit OAuth
    when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set (metered by token, not
    IP), otherwise falls back to unauthenticated RSS. All search terms for a
    subreddit are combined into a single OR query to keep request volume low.
    Returns events for new threads not seen in the previous snapshot.
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
        for item in _search_subreddit(subreddit, query):
            post_id = item["id"]
            if not post_id or post_id in seen_ids:
                continue

            new_seen_ids.add(post_id)
            events.append({
                "company": company,
                "signal_type": "reddit",
                "source": item["link"],
                "subreddit": subreddit,
                "search_term": _matched_term(item, all_terms, company_name),
                "alert": alert_level,
                "raw_diff": _post_summary(item),
                "title": item["title"],
            })

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
