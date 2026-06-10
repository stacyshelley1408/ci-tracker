import hashlib
import json
import time
from pathlib import Path
import feedparser
import requests

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"
REDDIT_BASE = "https://www.reddit.com"
# RSS is far less aggressively blocked from CI IP ranges than the JSON API.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ci-tracker/1.0; +https://github.com/stacyshelley1408/ci-tracker)"
}


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
    import re
    summary_text = re.sub(r"<[^>]+>", " ", summary).strip()[:300]
    link = getattr(entry, "link", "")
    return f"{title}\n{summary_text}\n{link}".strip()


def _search_subreddit_rss(subreddit: str, query: str) -> list:
    url = f"{REDDIT_BASE}/r/{subreddit}/search.rss"
    params = {"q": query, "sort": "new", "t": "week", "restrict_sr": "1", "limit": "25"}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        return feed.entries
    except Exception as e:
        print(f"[reddit] failed r/{subreddit} query '{query}': {e}")
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
    Returns events for new threads not seen in previous snapshot.
    """
    events = []
    snapshot_path = _snapshot_path(company)
    seen_ids = _load_snapshot(snapshot_path)
    new_seen_ids = set(seen_ids)

    all_terms = [company_name] + [t for t in search_terms if t != company_name]

    for subreddit in subreddits:
        for term in all_terms:
            entries = _search_subreddit_rss(subreddit, term)

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
                    "search_term": term,
                    "alert": alert_level,
                    "raw_diff": _post_summary(entry),
                    "title": getattr(entry, "title", ""),
                })

            time.sleep(1)

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
