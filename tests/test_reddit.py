"""Tests for the pure match logic in src/signals/reddit.py.

Run with:  python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from signals.reddit import _matches_thread, _post_id, _subreddit  # noqa: E402


class TestMatchesThread(unittest.TestCase):
    def test_term_in_title_matches(self):
        self.assertTrue(_matches_thread("ServiceNow GRC", "ServiceNow GRC vs Archer: which to pick?"))

    def test_case_insensitive(self):
        self.assertTrue(_matches_thread("metricstream", "Anyone migrated off MetricStream?"))

    def test_ad_in_body_not_title_is_rejected(self):
        # The real false positive: an unrelated r/PESU thread whose title is
        # about an IoT syllabus. The competitor name only appears in Tavily's
        # body field, lifted from Reddit's promoted/recommended rail -- it must
        # NOT match, because the title (gate) never names the company.
        title = "regarding updated iot syllabus : r/PESU - Reddit"
        self.assertFalse(_matches_thread("ServiceNow GRC", title))

    def test_empty_title_is_rejected(self):
        self.assertFalse(_matches_thread("ServiceNow GRC", ""))


class TestPostId(unittest.TestCase):
    def test_native_t3_id_from_permalink(self):
        link = "https://www.reddit.com/r/PESU/comments/1uaoscm/regarding_updated_iot_syllabus"
        self.assertEqual(_post_id(link), "t3_1uaoscm")

    def test_subreddit_parsed(self):
        link = "https://www.reddit.com/r/grc/comments/abc123/title"
        self.assertEqual(_subreddit(link), "grc")


if __name__ == "__main__":
    unittest.main()
