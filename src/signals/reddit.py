import hashlib
import html
import json
import os
import re
import time
from pathlib import Path
import requests

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"

# Reddit content is collected through Tavily's search index instead of reddit.com
# directly. Direct Reddit access (RSS or OAuth) is throttled hard from CI runner
# egress IPs; Google's official search APIs are closed to new users; Bing's and
# Brave's free tiers were retired. Tavily offers a genuine recurring free tier
# (1,000 credits/month, no card) and an `include_domains` filter that scopes a
# query to reddit.com. The tradeoff vs. Google is index depth, acceptable here.
# Requires TAVILY_API_KEY; absent it, the signal skips gracefully.
TAVILY_ENDPOINT = "https://api.tavily.com/search"
TIME_RANGE = "week"        # last 7 days, matching the previous lookback window
MAX_RESULTS = 20           # Tavily hard cap per request; cost is 1 credit either way

_SUBREDDIT_RE = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)", re.IGNORECASE)
_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)", re.IGNORECASE)


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


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def _post_id(link: str) -> str:
    # Reuse Reddit's native t3_<id> scheme when the permalink exposes it, so dedup
    # history carries over from the old OAuth path; otherwise hash the URL.
    m = _POST_ID_RE.search(link)
    if m:
        return f"t3_{m.group(1).lower()}"
    return hashlib.md5(link.encode()).hexdigest() if link else ""


def _subreddit(link: str) -> str:
    m = _SUBREDDIT_RE.search(link)
    return m.group(1).lower() if m else ""


def _matches_thread(term: str, title: str) -> bool:
    # Decide whether `term` actually identifies the LINKED thread. Match on the
    # title only -- never the body. Tavily's `content` field for a Reddit page
    # bleeds in Reddit's inline "promoted"/"recommended posts" rail, which is
    # foreign to the thread the URL points at. A GRC vendor ad naming every
    # competitor ("For those using Archer, ServiceNow GRC, AuditBoard, or
    # MetricStream...") was matching threads on unrelated topics (e.g. an r/PESU
    # IoT-syllabus post) and firing false alerts. The post title is bound 1:1 to
    # the thread (Reddit derives the URL slug from it), so it is the only field
    # safe to gate on.
    return term.lower() in title.lower()


def _search(api_key: str, term: str) -> list[dict]:
    payload = {
        "query": term,
        "search_depth": "basic",
        "topic": "general",
        "time_range": TIME_RANGE,
        "max_results": MAX_RESULTS,
        "include_domains": ["reddit.com"],
    }
    try:
        resp = requests.post(
            TAVILY_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[reddit] Tavily query '{term}' failed: {e}")
        return []


def check_reddit(
    company: str,
    search_terms: list[str],
    company_name: str,
    alert_level: str,
) -> list[dict]:
    """
    Searches Reddit for company mentions via the Tavily search API, scoped to
    reddit.com through include_domains, avoiding reddit.com's IP throttling of CI
    runners. One query per brand term (Tavily is semantic, not boolean). A hit is
    kept only if the searched term appears literally in the post title, since
    ranking is fuzzy and the body field is polluted by Reddit's promoted/
    recommended-post rail. Returns events for permalinks not seen in the previous
    snapshot. Skips silently if TAVILY_API_KEY is unset.
    """
    events = []
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        print("[reddit] TAVILY_API_KEY not set; skipping Reddit signal")
        return events

    terms = [company_name] + [t for t in search_terms if t != company_name]

    snapshot_path = _snapshot_path(company)
    seen_ids = _load_snapshot(snapshot_path)
    new_seen_ids = set(seen_ids)

    for term in terms:
        for hit in _search(api_key, term):
            link = hit.get("url", "")
            post_id = _post_id(link)
            # new_seen_ids is seeded from the snapshot and grown as we append, so
            # this one check skips both prior-run hits and cross-term duplicates.
            if not post_id or post_id in new_seen_ids:
                continue

            title = _clean(hit.get("title", ""))
            content = _clean(hit.get("content", ""))
            # Ranking is fuzzy and the body field is contaminated by Reddit's
            # promoted/recommended rail, so require the literal term in the
            # title -- the only field bound to the linked thread.
            if not _matches_thread(term, title):
                continue

            new_seen_ids.add(post_id)
            events.append({
                "company": company,
                "signal_type": "reddit",
                "source": link,
                "subreddit": _subreddit(link),
                "search_term": term,
                "alert": alert_level,
                "raw_diff": f"{title}\n{content}\n{link}".strip(),
                "title": title or f"Reddit discussion ({term})",
            })

        time.sleep(0.2)  # gentle pacing between term queries

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
