import hashlib
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"

# Platform-specific selectors for rating + review count
PLATFORM_SELECTORS = {
    "g2": {
        "rating": "[data-test='rating-value'], .fw-semibold.f-1",
        "review_count": "[data-test='review-count'], .filters-heading",
        "reviews": "article[itemprop='review'], .paper.paper--white.paper--shadow",
        "review_id_attr": "data-id",
    },
    "gartner_peer_insights": {
        "rating": ".overall-rating .rating-score, .hero-rating-score",
        "review_count": ".review-count, .ratings-count",
        "reviews": ".review-content, .peer-insight",
        "review_id_attr": "data-review-id",
    },
    "capterra": {
        "rating": ".overall-rating, [data-testid='overall-score']",
        "review_count": ".review-count-text, .total-review-count",
        "reviews": ".review-card, article.review",
        "review_id_attr": "data-review-id",
    },
    "trustradius": {
        "rating": ".overall-score, .trScore",
        "review_count": ".review-count, .total-reviews",
        "reviews": ".review-content, .reviewContent",
        "review_id_attr": "data-review-id",
    },
    "peerspot": {
        "rating": ".overall-rating, .rating-value",
        "review_count": ".reviews-count, .number-of-reviews",
        "reviews": ".review-item, .review-card",
        "review_id_attr": "data-id",
    },
    "spiceworks": {
        "rating": ".overall-rating, .star-rating-value",
        "review_count": ".review-count, .total-reviews",
        "reviews": ".review-post, .community-review",
        "review_id_attr": "data-post-id",
    },
}


def _snapshot_path(company: str, platform: str) -> Path:
    key = hashlib.md5(f"{company}:{platform}:reviews".encode()).hexdigest()
    return SNAPSHOTS_DIR / f"{company}_reviews_{platform}_{key}.json"


def _load_snapshot(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _save_snapshot(path: Path, data: dict):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _extract_number(text: str) -> float | None:
    match = re.search(r"[\d.]+", text.replace(",", ""))
    return float(match.group()) if match else None


def _scrape_platform(page, platform: str) -> dict:
    selectors = PLATFORM_SELECTORS.get(platform, {})
    result = {"rating": None, "review_count": None, "content_hash": None, "review_ids": []}

    # Overall rating
    if selectors.get("rating"):
        try:
            el = page.query_selector(selectors["rating"])
            if el:
                result["rating"] = _extract_number(el.inner_text())
        except Exception:
            pass

    # Review count
    if selectors.get("review_count"):
        try:
            el = page.query_selector(selectors["review_count"])
            if el:
                result["review_count"] = _extract_number(el.inner_text())
        except Exception:
            pass

    # Review IDs for new-review detection
    if selectors.get("reviews") and selectors.get("review_id_attr"):
        try:
            review_els = page.query_selector_all(selectors["reviews"])
            for el in review_els[:20]:
                rid = el.get_attribute(selectors["review_id_attr"])
                if rid:
                    result["review_ids"].append(rid)
                else:
                    # Fall back to hash of review text
                    text = el.inner_text()[:200]
                    result["review_ids"].append(hashlib.md5(text.encode()).hexdigest())
        except Exception:
            pass

    # Full page content hash as fallback change detector
    try:
        body_text = page.evaluate("() => { const el = document.body || document.documentElement; return el ? el.innerText.replace(/\\s+/g, ' ').trim() : ''; }")
        result["content_hash"] = hashlib.md5(body_text.encode()).hexdigest()
    except Exception:
        pass

    return result


def _build_diff_summary(previous: dict, current: dict) -> str:
    parts = []

    if previous.get("rating") and current.get("rating"):
        delta = round(current["rating"] - previous["rating"], 2)
        if delta != 0:
            direction = "up" if delta > 0 else "down"
            parts.append(f"Rating moved {direction} {abs(delta)} points ({previous['rating']} → {current['rating']})")

    if previous.get("review_count") and current.get("review_count"):
        delta = int(current["review_count"] - previous["review_count"])
        if delta > 0:
            parts.append(f"{delta} new review(s) (total: {int(current['review_count'])})")

    prev_ids = set(previous.get("review_ids", []))
    curr_ids = set(current.get("review_ids", []))
    new_ids = curr_ids - prev_ids
    if new_ids:
        parts.append(f"{len(new_ids)} new review ID(s) detected")

    if not parts and previous.get("content_hash") != current.get("content_hash"):
        parts.append("Page content changed (rating/count extraction unavailable)")

    return "; ".join(parts) if parts else ""


def check_reviews(company: str, platforms: list[str], review_urls: dict, alert_level: str) -> list[dict]:
    """
    Scrapes review platforms, diffs against previous snapshot.
    Returns events for rating changes, new reviews, or page content changes.
    """
    events = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )

        for platform in platforms:
            url = review_urls.get(platform)
            if not url:
                continue

            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)

                current = _scrape_platform(page, platform)
                snapshot_path = _snapshot_path(company, platform)
                previous = _load_snapshot(snapshot_path)

                if previous is None:
                    _save_snapshot(snapshot_path, current)
                else:
                    changed = (
                        previous.get("content_hash") != current.get("content_hash")
                    )
                    if changed:
                        diff_summary = _build_diff_summary(previous, current)
                        _save_snapshot(snapshot_path, current)
                        if diff_summary:
                            events.append({
                                "company": company,
                                "signal_type": "review",
                                "source": url,
                                "platform": platform,
                                "alert": alert_level,
                                "raw_diff": diff_summary,
                                "previous_rating": previous.get("rating"),
                                "current_rating": current.get("rating"),
                                "previous_review_count": previous.get("review_count"),
                                "current_review_count": current.get("review_count"),
                            })

                page.close()

            except Exception as e:
                print(f"[reviews] failed {platform} / {url}: {e}")
                continue

        browser.close()

    return events
