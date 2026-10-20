import unittest
from knoblog import why as W


class WhyTests(unittest.TestCase):
    def test_pr_from_message(self):
        self.assertEqual(W.pr_from_message("Fix costmap inflation (#4821)"), 4821)
        self.assertEqual(W.pr_from_message("Merge pull request #12 from a/b"), 12)
        self.assertIsNone(W.pr_from_message("update params"))

    def test_slug(self):
        self.assertEqual(W.github_slug("https://github.com/PX4/PX4-Autopilot"), "PX4/PX4-Autopilot")
        self.assertIsNone(W.github_slug("https://gitlab.com/a/b"))

    def test_annotate_offline(self):
        e = {"sha": "a" * 40, "subject": "Raise inflation radius (#7)", "body": "Robots clipped doors.\n\nSigned-off-by: x"}
        w = W.annotate(e, "https://github.com/o/r", W.PRLookup("o/r", enabled=False))
        self.assertEqual(w["pr_url"], "https://github.com/o/r/pull/7")
        self.assertEqual(w["pr_source"], "commit-message")
        self.assertEqual(w["message"], "Robots clipped doors.")

    def test_cache_hit_wins(self):
        lk = W.PRLookup("o/r", enabled=False)
        lk.cache["b" * 40] = {"number": 9, "url": "https://github.com/o/r/pull/9", "title": "t"}
        w = W.annotate({"sha": "b" * 40, "subject": "squashed", "body": ""}, "https://github.com/o/r", lk)
        self.assertEqual((w["pr_number"], w["pr_source"]), (9, "github-api"))


if __name__ == "__main__":
    unittest.main()
