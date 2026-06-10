import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

SNAPSHOTS_DIR = Path(__file__).parents[2] / "snapshots"


def _snapshot_path(company: str) -> Path:
    key = hashlib.md5(f"{company}:jobs".encode()).hexdigest()
    return SNAPSHOTS_DIR / f"{company}_jobs_{key}.json"


def _load_snapshot(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None


def _save_snapshot(path: Path, jobs: list[dict]):
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(jobs, indent=2))


def _matches_filters(title: str, filters: dict) -> bool:
    if not filters:
        return True

    departments = [d.lower() for d in filters.get("departments", [])]
    seniority = [s.lower() for s in filters.get("seniority", [])]
    title_lower = title.lower()

    dept_match = not departments or any(d in title_lower for d in departments)
    seniority_match = not seniority or any(s in title_lower for s in seniority)

    return dept_match and seniority_match


def _extract_jobs(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    # Common patterns across ATS platforms and custom career pages
    candidates = (
        soup.find_all("a", href=True, string=True) +
        soup.find_all(class_=lambda c: c and any(
            term in c.lower() for term in ["job", "position", "role", "opening", "career"]
        ))
    )

    seen = set()
    for el in candidates:
        title = el.get_text(strip=True)
        href = el.get("href", "")
        if len(title) < 5 or len(title) > 200:
            continue
        if title in seen:
            continue
        # Filter out nav/footer noise
        if any(skip in title.lower() for skip in ["home", "about", "contact", "login", "sign in", "blog"]):
            continue
        seen.add(title)
        jobs.append({"title": title, "url": href})

    return jobs


def check_jobs(company: str, careers_url: str, filters: dict, alert_level: str) -> list[dict]:
    """
    Scrapes careers page, diffs against previous snapshot.
    Returns events for new job postings that match filters.
    """
    events = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )

        try:
            page = context.new_page()
            page.goto(careers_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)

            html = page.content()
            all_jobs = _extract_jobs(html)
            filtered_jobs = [j for j in all_jobs if _matches_filters(j["title"], filters)]

            snapshot_path = _snapshot_path(company)
            previous = _load_snapshot(snapshot_path)

            if previous is None:
                _save_snapshot(snapshot_path, filtered_jobs)
            else:
                previous_titles = {j["title"] for j in previous}
                new_jobs = [j for j in filtered_jobs if j["title"] not in previous_titles]
                _save_snapshot(snapshot_path, filtered_jobs)

                for job in new_jobs:
                    events.append({
                        "company": company,
                        "signal_type": "job_posting",
                        "source": careers_url,
                        "alert": alert_level,
                        "raw_diff": f"New posting: {job['title']}",
                        "job_title": job["title"],
                        "job_url": job["url"],
                    })

        except Exception as e:
            print(f"[jobs] failed {careers_url}: {e}")

        browser.close()

    return events
