import copy
import unittest

from knoblog import config as C
from knoblog import posttext as P


def cfg(**post):
    c = C.load(overrides={"name": "Robot", "post": post, "site": {"url": "https://example.github.io/robot/"}})
    return c


def entry(changes, **kw):
    e = {"sha": "a" * 40, "short": "aaaaaaa", "url": "https://github.com/example/robot/commit/" + "a" * 40,
         "date": "2026-03-05T12:00:00+00:00", "class": "code", "is_root": False, "changes": changes,
         "components": {"added": [], "removed": [], "renamed": [], "changed": []}, "stats": {"files": 1}}
    e.update(kw)
    return e


def ch(name, old, new, kind="changed", extractor="ros2_yaml", file="config/p.yaml", type_="number"):
    return {"extractor": extractor, "file": file, "name": name, "keyed": extractor in ("ros2_yaml", "yaml"), "kind": kind,
            "old": old, "new": new, "type": type_}


class WeightedLength(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(P.weighted_len("abc"), 3)
        self.assertEqual(P.weighted_len("→"), 2)
        self.assertEqual(P.weighted_len("see https://example.com/a/very/long/path/that/is/long"), 4 + 23)
        self.assertEqual(P.weighted_len("param.rs"), 23)  # bare domain-like token counted as a link
        self.assertEqual(P.weighted_len("max_vel_x/kp"), 12)


class Facts(unittest.TestCase):
    def test_numeric_change_and_suffix(self):
        r = P.compose(entry([ch("controller.kp", "0.5", "1.0")]), cfg())
        self.assertIn("controller/kp 0.5 → 1.0 (2x higher)", r["posts"][0])
        self.assertTrue(r["posts"][0].startswith("Robot update (Mar 5): "))

    def test_min_change_pct(self):
        r = P.compose(entry([ch("a.b", "100", "101")]), cfg(min_change_pct=5))
        self.assertEqual(r["posts"], [])

    def test_conflicting_mirrors_are_ambiguous(self):
        e = entry([ch("a.kp", "1", "2", file="x.yaml"), ch("a.kp", "1", "3", file="y.yaml")])
        with self.assertRaises(P.Ambiguous):
            P.compose(e, cfg())

    def test_identical_mirrors_once(self):
        e = entry([ch("a.kp", "1", "2", file="x.yaml"), ch("a.kp", "1", "2", file="y.yaml")])
        self.assertEqual(P.compose(e, cfg())["posts"][0].count("a/kp"), 1)

    def test_bulk_and_root(self):
        many = [ch(f"n.p{i}", None, "1", kind="added") for i in range(60)]
        with self.assertRaises(P.Ambiguous):
            P.compose(entry(many), cfg())
        with self.assertRaises(P.Ambiguous):
            P.compose(entry([ch("a.b", "1", "2")], is_root=True), cfg())

    def test_flag_flip(self):
        r = P.compose(entry([ch("safety.enabled", "true", "false", type_="bool")]), cfg())
        self.assertIn("safety/enabled: switched OFF by default", r["posts"][0])

    def test_thread_split(self):
        long = [ch(f"some_long_node_name.with_a_long_parameter_name_{i}", "1.0", "2.0") for i in range(4)]
        r = P.compose(entry(long), cfg())
        self.assertGreater(len(r["posts"]), 1)
        self.assertTrue(all(P.weighted_len(p) <= 280 for p in r["posts"]))
        self.assertIn("Commit: https://github.com/", r["posts"][-1])


class Gates(unittest.TestCase):
    def test_unverified_number_rejected(self):
        e = entry([ch("a.b", "1", "2")])
        with self.assertRaises(P.Ambiguous):
            P.check_posts(["Robot update: a/b 1 → 7"], e, P.Derived(), "Robot update: ", cfg()["post"])

    def test_derived_numbers_allowed(self):
        e = entry([ch("a.b", "1.0e-10", "2.0e-10")])
        r = P.compose(e, cfg())
        self.assertIn("1.0e-10 → 2.0e-10 (2x higher)", r["posts"][0])

    def test_mentions_and_bare_domains_rejected(self):
        e = entry([ch("a.b", "1", "2")])
        pc = cfg()["post"]
        with self.assertRaises(P.Ambiguous):
            P.check_posts(["hi @someone 1 2 " + pc["disclaimer"]], e, P.Derived(), "", pc)
        with self.assertRaises(P.Ambiguous):
            P.check_posts(["see params.yaml 1 2 " + pc["disclaimer"]], e, P.Derived(), "", pc)

    def test_note_title_must_be_verified(self):
        e = entry([ch("a.b", "1", "2")], summary={"source": "note", "title": "Gain doubled from 1 to 2"})
        self.assertEqual(P.verified_note_title(e), "Gain doubled from 1 to 2")
        e2 = copy.deepcopy(e)
        e2["summary"]["title"] = "Gain set to 3"
        self.assertIsNone(P.verified_note_title(e2))


class Highlight(unittest.TestCase):
    def test_human_mode_weight_label(self):
        c = C.load("x-algorithm")
        e = entry([ch("FavoriteWeight", "0.5", "1.0", extractor="rust_param", file="home-mixer/params/param.rs")])
        e["date"] = "2026-08-14T12:00:00+00:00"
        r = P.compose(e, c)
        self.assertIn('X algorithm update (Aug 14): "favorite" weight 0.5 → 1 (2x higher)', r["posts"][0])


if __name__ == "__main__":
    unittest.main()
