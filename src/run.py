import argparse
import sys
from pathlib import Path

import yaml

from classify import classify
from storage import (
    get_alertable_events,
    get_digest_events,
    get_highlights,
    mark_alerted,
    mark_in_digest,
    save_event,
)
from alert import send_alert
from digest import send_digest
from signals.messaging import check_messaging
from signals.jobs import check_jobs
from signals.news import check_news
from signals.reviews import check_reviews
from signals.reddit import check_reddit

CONFIG_PATH = Path(__file__).parents[1] / "config" / "companies.yml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def resolve(company_val, default_val):
    """Return company-level value if set, otherwise default."""
    if company_val is None:
        return default_val
    return company_val


def resolve_signal(company_signals: dict, signal_key: str, defaults: dict) -> dict:
    default_signal = defaults.get("signals", {}).get(signal_key, {})
    company_signal = company_signals.get(signal_key, {})
    return {**default_signal, **company_signal}


def run_company(company: dict, defaults: dict, mode: str) -> list[dict]:
    name = company["name"]
    base_url = company.get("base_url", "")
    active = resolve(company.get("active"), defaults.get("active", True))
    threshold = resolve(company.get("significance_threshold"), defaults.get("significance_threshold", 4))
    watch_keywords = resolve(company.get("watch_keywords"), defaults.get("watch_keywords", []))
    company_signals = company.get("signals", {})

    if not active:
        print(f"[run] skipping {name} (inactive)")
        return []

    print(f"[run] processing {name}")
    events = []

    # Messaging diffs
    pages = company.get("pages", [])
    if pages and base_url:
        sig = resolve_signal(company_signals, "messaging_diffs", defaults)
        raw_events = check_messaging(name, base_url, pages)
        for e in raw_events:
            # Per-page alert level may override signal default
            e.setdefault("alert", sig.get("alert", "weekly"))
            e["include_in_digest"] = sig.get("include_in_digest", True)
            events.append(e)

    # Job postings
    careers_url = company.get("careers_url")
    if careers_url:
        sig = resolve_signal(company_signals, "job_postings", defaults)
        filters = sig.get("filters", {})
        raw_events = check_jobs(name, careers_url, filters, sig.get("alert", "weekly"))
        for e in raw_events:
            e["include_in_digest"] = sig.get("include_in_digest", True)
            events.append(e)

    # News / RSS
    news_feeds = company.get("news_feeds", [])
    if news_feeds:
        sig = resolve_signal(company_signals, "news", defaults)
        raw_events = check_news(name, news_feeds, watch_keywords, sig.get("alert", "daily"))
        for e in raw_events:
            e["include_in_digest"] = sig.get("include_in_digest", True)
            events.append(e)

    # Reviews
    sig = resolve_signal(company_signals, "reviews", defaults)
    platforms = sig.get("platforms", [])
    review_urls = company.get("review_urls", {})
    if platforms and review_urls:
        raw_events = check_reviews(name, platforms, review_urls, sig.get("alert", "weekly"))
        for e in raw_events:
            e["include_in_digest"] = sig.get("include_in_digest", True)
            events.append(e)

    # Reddit
    sig = resolve_signal(company_signals, "reddit", defaults)
    subreddits = sig.get("subreddits", [])
    search_terms = sig.get("search_terms", [])
    if subreddits:
        raw_events = check_reddit(
            name,
            subreddits,
            search_terms,
            name,
            sig.get("alert", "daily"),
        )
        for e in raw_events:
            e["include_in_digest"] = sig.get("include_in_digest", True)
            events.append(e)

    # Classify and save all events
    saved = []
    for event in events:
        classified = classify(event, watch_keywords)
        saved_event = save_event(classified)
        saved.append(saved_event)
        print(f"  [{classified.get('signal_type')}] score={classified.get('haiku_score')} "
              f"category={classified.get('haiku_category')} source={event.get('source', '')[:60]}")

    return saved


def run_daily(config: dict):
    defaults = config.get("defaults", {})
    companies = config.get("companies", [])

    all_saved = []
    for company in companies:
        saved = run_company(company, defaults, mode="daily")
        all_saved.extend(saved)

    print(f"\n[run] {len(all_saved)} total event(s) saved")

    # Alert on significant daily events
    company_thresholds = {
        c["name"]: c.get("significance_threshold", defaults.get("significance_threshold", 4))
        for c in companies
    }
    default_threshold = defaults.get("significance_threshold", 4)

    alertable = get_alertable_events(threshold=1)  # fetch all, filter per company below
    to_alert = [
        e for e in alertable
        if e.get("haiku_score", 0) >= company_thresholds.get(e.get("company"), default_threshold)
    ]

    if to_alert:
        alerted_ids = send_alert(to_alert)
        for eid in alerted_ids:
            mark_alerted(eid)
    else:
        print("[run] no alertable events")


def run_weekly(config: dict):
    # Run all signals first
    run_daily(config)

    # Then generate and send digest
    digest_events = get_digest_events(days=7)
    highlights = get_highlights(days=7, min_score=4)

    if digest_events:
        digest_ids = send_digest(highlights, digest_events)
        for eid in digest_ids:
            mark_in_digest(eid)
    else:
        print("[run] no events for digest")


def main():
    parser = argparse.ArgumentParser(description="CI Tracker")
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly"],
        default="daily",
        help="Run mode: daily (signals + alerts) or weekly (signals + alerts + digest)",
    )
    args = parser.parse_args()

    config = load_config()
    print(f"[run] starting in {args.mode} mode")

    if args.mode == "daily":
        run_daily(config)
    elif args.mode == "weekly":
        run_weekly(config)


if __name__ == "__main__":
    main()
