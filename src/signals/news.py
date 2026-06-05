import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import feedparser

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"


def _snapshot_path(company: str) -> Path:
    key = hashlib.md5(f"{company}:news".encode()).hexdigest()
    return SNAPSHOTS_DIR / f"{company}_news_{key}.json"


def _load_snapshot(path: Path) -> set:
    if path.exists():
        return set(json.loads(path.read_text()))
    return set()


def _save_snapshot(path: Path, seen_ids: set):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(list(seen_ids)))


def _entry_id(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def _parse_published(entry) -> str:
    published = entry.get("published", "")
    if published:
        return published
    updated = entry.get("updated", "")
    if updated:
        return updated
    return datetime.now(timezone.utc).isoformat()


def check_news(company: str, feeds: list[str], watch_keywords: list[str], alert_level: str) -> list[dict]:
    """
    Polls RSS feeds, returns events for new entries.
    Boosts alert level if watch_keywords appear in title or summary.
    """
    events = []
    snapshot_path = _snapshot_path(company)
    seen_ids = _load_snapshot(snapshot_path)
    new_seen_ids = set(seen_ids)

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries:
                entry_id = _entry_id(entry)
                if not entry_id or entry_id in seen_ids:
                    continue

                new_seen_ids.add(entry_id)
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                published = _parse_published(entry)

                # Boost alert if watch keywords appear
                combined = f"{title} {summary}".lower()
                keyword_hit = any(kw.lower() in combined for kw in watch_keywords)
                effective_alert = "daily" if keyword_hit else alert_level

                events.append({
                    "company": company,
                    "signal_type": "news",
                    "source": link,
                    "alert": effective_alert,
                    "raw_diff": f"{title}\n{summary[:300]}".strip(),
                    "title": title,
                    "published": published,
                    "keyword_hit": keyword_hit,
                })

        except Exception as e:
            print(f"[news] failed {feed_url}: {e}")
            continue

    _save_snapshot(snapshot_path, new_seen_ids)
    return events
