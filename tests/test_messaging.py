"""Tests for the pure diff/noise logic in src/signals/messaging.py.

Run with:  python -m pytest tests/  (or: python -m unittest discover tests)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from signals.messaging import _diff_text, _is_noise  # noqa: E402


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
            "Archer launches new AI governance module for continuous compliance",
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


if __name__ == "__main__":
    unittest.main()
