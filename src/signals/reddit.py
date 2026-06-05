import hashlib
import json
import time
from pathlib import Path
import requests

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"
REDDIT_BASE = "https://www.reddit.com"
HEADERS = {"User-Agent": "ci-tracker/1.0 (competitive intelligence research tool)"}


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


def _search_subreddit(subreddit: str, query: str) -> list[dict]:
    url = f"{REDDIT_BASE}/r/{subreddit}/search.json"
    params = {
        "q": query,
        "sort": "new",
        "t": "week",
        "limit": 25,
        "restrict_sr": 1,
    }
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("data", {}).get("children", [])
    except Exception as e:
        print(f"[reddit] failed r/{subreddit} query '{query}': {e}")
        return []


def _post_id(post: dict) -> str:
    return post.get("data", {}).get("id", "")


def _post_summary(post: dict) -> str:
    data = post.get("data", {})
    title = data.get("title", "")
    selftext = data.get("selftext", "")[:300]
    score = data.get("score", 0)
    num_comments = data.get("num_comments", 0)
    url = f"{REDDIT_BASE}{data.get('permalink', '')}"
    return f"{title}\n{selftext}\nScore: {score} | Comments: {num_comments}\n{url}".strip()


def _is_meaningful(post: dict) -> bool:
    data = post.get("data", {})
    score = data.get("score", 0)
    num_comments = data.get("num_comments", 0)
    # Skip low-engagement posts to reduce noise
    return score >= 2 or num_comments >= 1


def check_reddit(
    company: str,
    subreddits: list[str],
    search_terms: list[str],
    company_name: str,
    alert_level: str,
) -> list[dict]:
    """
    Searches subreddits for company mentions and custom search terms.
    Returns events for new threads not seen in previous snapshot.
    """
    events = []
    snapshot_path = _snapshot_path(company)
    seen_ids = _load_snapshot(snapshot_path)
    new_seen_ids = set(seen_ids)

    # Always search by company name plus any configured extra terms
    all_terms = [company_name] + [t for t in search_terms if t != company_name]

    for subreddit in subreddits:
        for term in all_terms:
            posts = _search_subreddit(subreddit, term)

            for post in posts:
                post_id = _post_id(post)
                if not post_id or post_id in seen_ids:
                    continue
                if not _is_meaningful(post):
                    new_seen_ids.add(post_id)
                    continue

                new_seen_ids.add(post_id)
                data = post.get("data", {})
                permalink = f"{REDDIT_BASE}{data.get('permalink', '')}"

                events.append({
                    "company": company,
                    "signal_type": "reddit",
                    "source": permalink,
                    "subreddit": subreddit,
                    "search_term": term,
                    "alert": alert_level,
                    "raw_diff": _post_summary(post),
                    "title": data.get("title", ""),
                    "score": data.get("score", 0),
                    "num_comments": data.get("num_comments", 0),
                })

            # Respect Reddit's rate limit between requests
            time.sleep(1)

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
