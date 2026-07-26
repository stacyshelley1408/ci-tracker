"""Tests for the pure diff/noise logic in src/signals/messaging.py.

Run with:  python -m pytest tests/  (or: python -m unittest discover tests)
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from signals.messaging import (  # noqa: E402
    MONITOR_FAILURE_THRESHOLD,
    _diff_text,
    _has_baseline,
    _is_error_page,
    _is_noise,
    _monitor_health_event,
    _record_failure,
    _save_snapshot,
    _should_warn,
)


class TestIsNoise(unittest.TestCase):
    def test_cookie_consent_boilerplate_is_noise(self):
        # Fragments lifted verbatim from a real false-positive OneTrust alert.
        noisy = [
            "Cookie Notice Accept allEssential onlyCustomize Settings Opt-Out Request Honored",
            "User ID: 35da8ea0-fc10-45dc-ac01-569a64dfc770 Manage Consent Preferences",
            "These cookies are set by our advertising partners to provide behavioral advertising",
            "When you visit any website, it may store or retrieve information on your browser",
            "Strictly Necessary Cookies Always Active",
        ]
        for fragment in noisy:
            self.assertTrue(_is_noise(fragment), f"should be noise: {fragment!r}")

    def test_real_signal_is_not_noise(self):
        signal = [
            "LogicGate launches new AI governance module for continuous compliance",
            "Now trusted by 14,000 customers worldwide",
            "Introducing usage-based pricing for mid-market teams",
        ]
        for fragment in signal:
            self.assertFalse(_is_noise(fragment), f"should be signal: {fragment!r}")

    def test_is_case_insensitive(self):
        self.assertTrue(_is_noise("ACCEPT ALL cookies"))

    def test_iab_category_descriptions_are_noise(self):
        # Verbatim fragments that leaked into a real OneTrust alert — note none
        # of these contain the words "cookie" or "consent".
        leaked = [
            "Click on the different category headings to learn more and change our default settings",
            "The information does not usually identify you directly, but it can give you a more personalized web experience",
            "The profile created regarding your browsing interest and behavior is used to customize the ads you see when you access other websites",
            "They are usually set to manage actions made by you, such as requesting website visual elements",
            "They collect any type of browsing information necessary to create profiles and to understand user habits in order to develop an individual and specific advertising routine",
            "This information might be about you, your preferences, or your device, and is mostly used to make the site work as you expect",
            "to deliver content, maintain security, enable user choice, improve our sites, and for marketing purposes",
        ]
        for fragment in leaked:
            self.assertTrue(_is_noise(fragment), f"should be noise: {fragment!r}")


class TestDiffText(unittest.TestCase):
    def test_noise_only_change_yields_empty_diff(self):
        old = "Welcome to OneTrust. Trusted by 14000 customers."
        new = ("Welcome to OneTrust. Trusted by 14000 customers. "
               "Cookie Notice Accept all. User ID: abc-123 Manage Consent Preferences.")
        self.assertEqual(_diff_text(old, new), "")

    def test_real_change_survives_alongside_noise(self):
        old = "Welcome to OneTrust. Trusted by 14000 customers."
        new = ("Welcome to OneTrust. Trusted by 14000 customers. "
               "Cookie Notice Accept all. User ID: abc-123. "
               "We added a new pricing tier")
        result = _diff_text(old, new)
        self.assertIn("We added a new pricing tier", result)
        self.assertNotIn("Cookie Notice", result)
        self.assertNotIn("User ID", result)

    def test_no_change_yields_empty_diff(self):
        text = "Identical content here. Second sentence."
        self.assertEqual(_diff_text(text, text), "")

    def test_output_is_deterministic(self):
        # Common first/last sentence so only the middle insertions diff
        # (avoids the trailing-period artifact on the final token).
        old = "start. end."
        new = "start. zebra. alpha. middle. end."
        self.assertEqual(_diff_text(old, new), _diff_text(old, new))
        # sorted -> alphabetical order regardless of set iteration
        self.assertEqual(
            _diff_text(old, new),
            "Added: alpha | middle | zebra",
        )


class TestIsErrorPage(unittest.TestCase):
    def test_akamai_reference_page_is_error(self):
        # The body that produced the bogus ServiceNow "MESSAGING SHIFT" 3/5.
        page = ("Access Denied You don't have permission to access this "
                "resource. Reference #18.840c0317.1781805701.2a0a1b99 "
                "https://errors.edgesuite.net/18.840c0317.1781805701.2a0a1b99")
        self.assertTrue(_is_error_page(page))

    def test_cloudfront_and_cloudflare_pages_are_errors(self):
        self.assertTrue(_is_error_page("The request could not be satisfied. Request blocked."))
        self.assertTrue(_is_error_page("Attention Required! Cloudflare. Checking your browser before accessing."))
        self.assertTrue(_is_error_page("502 Bad Gateway nginx"))

    def test_real_page_is_not_error(self):
        page = "Integrated Risk Management. " * 200  # long, no error signature
        self.assertFalse(_is_error_page(page))

    def test_long_page_quoting_error_phrase_is_not_error(self):
        # A real article that happens to mention "access denied" should survive,
        # because it is long. Length gate protects legitimate content.
        page = ("Our security blog covers what an access denied response means. " * 60)
        self.assertFalse(_is_error_page(page))


class TestMonitorHealth(unittest.TestCase):
    """The consecutive-failure warning: a dead monitor must not read as a quiet competitor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "snap.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_failures_accumulate(self):
        self.assertEqual(_record_failure(self.path, "error page"), 1)
        self.assertEqual(_record_failure(self.path, "error page"), 2)
        self.assertEqual(_record_failure(self.path, "error page"), 3)

    def test_failure_preserves_the_good_baseline(self):
        # The reason a bad fetch is treated as a failure is that the last good
        # content survives it. Recording the failure must not disturb that.
        _save_snapshot(self.path, "real page text", "hash123")
        _record_failure(self.path, "CDN or WAF error page")
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["content"], "real page text")
        self.assertEqual(saved["hash"], "hash123")
        self.assertEqual(saved["consecutive_failures"], 1)
        self.assertEqual(saved["last_failure_reason"], "CDN or WAF error page")

    def test_success_resets_the_streak(self):
        _record_failure(self.path, "fetch error")
        _record_failure(self.path, "fetch error")
        _save_snapshot(self.path, "back up", "hash456")
        self.assertEqual(json.loads(self.path.read_text())["consecutive_failures"], 0)
        self.assertEqual(_record_failure(self.path, "fetch error"), 1)

    def test_failure_only_snapshot_is_not_a_baseline(self):
        # A URL whose very first fetch failed has a snapshot file but no content.
        # Diffing against it would report a wholesale rewrite on the next success.
        _record_failure(self.path, "fetch error")
        self.assertFalse(_has_baseline(json.loads(self.path.read_text())))
        self.assertFalse(_has_baseline(None))
        self.assertFalse(_has_baseline({"content": ""}))
        self.assertTrue(_has_baseline({"content": "real text"}))

    def test_poisoned_baseline_is_rejected(self):
        # The exact body stored for all three ServiceNow messaging URLs on the
        # data branch, saved before the error-page filter shipped. Diffing a
        # recovered page against it fires the wholesale false shift the filter
        # exists to prevent, so it must not count as a baseline.
        poisoned = ('Access Denied You don\'t have permission to access '
                    '"http://www.servicenow.com/solutions/governance-risk-compliance.html" '
                    'on this server. Reference #18.635ed617.1780849033.e90d36d')
        self.assertFalse(_has_baseline({"content": poisoned}))
        # A real page is long, so the length gate keeps it safe from this check.
        self.assertTrue(_has_baseline({"content": "Real GRC marketing copy. " * 100}))

    def test_warns_on_crossing_then_weekly(self):
        # Silent below the threshold, one warning on the crossing, then weekly.
        # Warning every run would have meant 35 alerts for a 5-week outage.
        for n in range(1, MONITOR_FAILURE_THRESHOLD):
            self.assertFalse(_should_warn(n))
        self.assertTrue(_should_warn(MONITOR_FAILURE_THRESHOLD))
        self.assertFalse(_should_warn(MONITOR_FAILURE_THRESHOLD + 1))
        self.assertTrue(_should_warn(7))
        self.assertTrue(_should_warn(14))
        self.assertFalse(_should_warn(15))

    def test_event_is_prescored_so_it_survives_thresholds(self):
        # Configured significance thresholds are 3 and 4, so the warning has to
        # clear 4 to reach an alert on every company.
        ev = _monitor_health_event("ServiceNow GRC", "https://x.test/grc", 5, "CDN or WAF error page", None)
        self.assertEqual(ev["signal_type"], "monitor_health")
        self.assertEqual(ev["haiku_category"], "monitor_health")
        self.assertGreaterEqual(ev["haiku_score"], 4)
        self.assertEqual(ev["alert"], "daily")
        self.assertIn("No successful fetch on record", ev["raw_diff"])

    def test_event_reports_the_last_good_fetch(self):
        ev = _monitor_health_event("Acme", "https://x.test/p", 3, "fetch error", "2026-06-23T11:00:00+00:00")
        self.assertIn("2026-06-23", ev["raw_diff"])

    def test_prescored_event_skips_the_classifier(self):
        # classify() must pass it through untouched: the category is a fact about
        # our collection, and a hallucinated low score would bury the alert.
        from classify import classify
        ev = _monitor_health_event("Acme", "https://x.test/p", 3, "fetch error", None)
        self.assertEqual(classify(ev, [])["haiku_score"], ev["haiku_score"])
        self.assertEqual(classify(ev, [])["haiku_category"], "monitor_health")


if __name__ == "__main__":
    unittest.main()
