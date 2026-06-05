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


def _diff_text(old: str, new: str) -> str:
    old_lines = set(old.split(". "))
    new_lines = set(new.split(". "))
    added = new_lines - old_lines
    removed = old_lines - new_lines
    parts = []
    if added:
        parts.append("Added: " + " | ".join(list(added)[:10]))
    if removed:
        parts.append("Removed: " + " | ".join(list(removed)[:10]))
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
