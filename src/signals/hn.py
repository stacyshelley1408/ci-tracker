import hashlib
import html
import json
import re
import time
from pathlib import Path
import requests

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"
# Hacker News search via the Algolia API: no auth, generous limits, and not
# IP-throttled the way Reddit is — a reliable community signal from CI runners.
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="
HEADERS = {"User-Agent": "ci-tracker/1.0 (competitive intelligence monitor)"}
LOOKBACK_DAYS = 7


def _snapshot_path(company: str) -> Path:
    key = hashlib.md5(f"{company}:hn".encode()).hexdigest()
    return SNAPSHOTS_DIR / f"{company}_hn_{key}.json"


def _load_snapshot(path: Path) -> set:
    if path.exists():
        return set(json.loads(path.read_text()))
    return set()


def _save_snapshot(path: Path, seen_ids: set):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(list(seen_ids)))


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def _search_hn(term: str) -> list:
    since = int(time.time()) - LOOKBACK_DAYS * 86400
    params = {
        "query": term,
        "tags": "(story,comment)",
        "numericFilters": f"created_at_i>{since}",
        "hitsPerPage": 25,
    }
    try:
        resp = requests.get(HN_SEARCH_URL, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[hn] failed query '{term}': {e}")
        return []


def check_hn(
    company: str,
    search_terms: list[str],
    company_name: str,
    alert_level: str,
) -> list[dict]:
    """
    Searches Hacker News (stories + comments) for company mentions via the
    Algolia API. Reuses the same brand search terms as the Reddit signal. A hit
    is kept only if the searched term appears literally in the text, since
    Algolia ranking is fuzzy — this keeps short/generic names from matching
    unrelated posts. Returns events for items not seen in the previous snapshot.
    """
    events = []
    snapshot_path = _snapshot_path(company)
    seen_ids = _load_snapshot(snapshot_path)
    new_seen_ids = set(seen_ids)

    terms = [company_name] + [t for t in search_terms if t != company_name]

    for term in terms:
        for hit in _search_hn(term):
            object_id = str(hit.get("objectID", ""))
            if not object_id or object_id in seen_ids:
                continue

            title = _clean(hit.get("title") or hit.get("story_title") or "")
            body = _clean(hit.get("story_text") or hit.get("comment_text") or "")
            combined = f"{title} {body}"
            # Algolia is fuzzy; require the literal term to drop false positives.
            if term.lower() not in combined.lower():
                continue

            new_seen_ids.add(object_id)
            link = hit.get("url") or f"{HN_ITEM_URL}{object_id}"
            raw_diff = f"{title}\n{body[:300]}\n{link}".strip()

            events.append({
                "company": company,
                "signal_type": "hn",
                "source": link,
                "search_term": term,
                "alert": alert_level,
                "raw_diff": raw_diff,
                "title": title or f"HN discussion ({term})",
            })

        time.sleep(0.2)  # gentle pacing; HN limits are generous but be polite

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
