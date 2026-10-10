"""End-to-end: a throwaway git repo with ROS 2 params, a C header and a PX4 params file -> site, feeds, post text."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from knoblog.cli import main

PARAMS_V1 = """controller_server:
  ros__parameters:
    controller_frequency: 20.0
    FollowPath:
      max_vel_x: 0.26
      xy_goal_tolerance: 0.25
      debug_trajectory_details: true
velocity_smoother:
  ros__parameters:
    max_accel: [2.5, 0.0, 3.2]
"""
PARAMS_V2 = PARAMS_V1.replace("max_vel_x: 0.26", "max_vel_x: 0.5").replace("[2.5, 0.0, 3.2]", "[3.0, 0.0, 3.2]") \
    .replace("debug_trajectory_details: true", "debug_trajectory_details: false")
PARAMS_V3 = PARAMS_V2.replace("controller_server:", "# tuned on the real robot\ncontroller_server:")  # comment only
HEADER_V1 = "#pragma once\n#define LOOP_HZ 400\nconstexpr double KP_ROLL = 0.15;\n"
HEADER_V2 = "#pragma once\n#define LOOP_HZ 800\nconstexpr double KP_ROLL = 0.15;\n"


def git(repo, *args, date="2026-01-01T12:00:00+00:00"):
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(repo, path, text):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        repo = cls.repo = os.path.join(cls.tmp, "robot")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        write(repo, "config/nav2_params.yaml", PARAMS_V1)
        write(repo, "include/robot/limits.hpp", HEADER_V1)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "initial", date="2026-01-01T12:00:00+00:00")
        write(repo, "config/nav2_params.yaml", PARAMS_V2)
        write(repo, "include/robot/limits.hpp", HEADER_V2)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "faster", date="2026-01-02T12:00:00+00:00")
        write(repo, "config/nav2_params.yaml", PARAMS_V3)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "comment", date="2026-01-03T12:00:00+00:00")
        cfg = os.path.join(cls.tmp, "knoblog.yml")
        with open(cfg, "w") as f:
            f.write("""extends: ros2
name: Robot
repo_url: https://github.com/example/robot
watch:
  paths: ['config/**/*.yaml', 'include/**/*.hpp']
site:
  url: https://example.github.io/robot/
""")
        cls.out = os.path.join(cls.tmp, "site")
        rc = main(["run", "--repo", repo, "--config", cfg, "--out", cls.out, "--no-fetch"])
        assert rc == 0
        cls.entries = json.loads(read(os.path.join(cls.out, "entries.json")))["entries"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def entry(self, subject_idx):
        return list(reversed(self.entries))[subject_idx]

    def test_classification(self):
        self.assertEqual(len(self.entries), 2, "comment-only commit is a no-op and is collapsed")
        self.assertTrue(self.entry(0)["is_root"])

    def test_changes(self):
        ch = {c["name"]: c for c in self.entry(1)["changes"]}
        self.assertEqual((ch["controller_server.FollowPath.max_vel_x"]["old"], ch["controller_server.FollowPath.max_vel_x"]["new"]), ("0.26", "0.5"))
        self.assertAlmostEqual(ch["controller_server.FollowPath.max_vel_x"]["pct"], (0.5 - 0.26) / 0.26 * 100, places=3)
        self.assertEqual((ch["velocity_smoother.max_accel[0]"]["old"], ch["velocity_smoother.max_accel[0]"]["new"]), ("2.5", "3.0"))
        self.assertNotIn("velocity_smoother.max_accel[2]", ch)
        self.assertEqual(ch["controller_server.FollowPath.debug_trajectory_details"]["new"], "false")
        self.assertEqual((ch["LOOP_HZ"]["old"], ch["LOOP_HZ"]["new"], ch["LOOP_HZ"]["extractor"]), ("400", "800", "c_const"))
        self.assertNotIn("KP_ROLL", ch)

    def test_changes_json(self):
        data = json.loads(read(os.path.join(self.out, "changes.json")))
        rec = next(r for r in data["changes"] if r["name"] == "LOOP_HZ")
        self.assertEqual(rec["pct_change"], 100.0)
        self.assertEqual(rec["ratio"], 2.0)
        self.assertTrue(rec["commit_url"].startswith("https://github.com/example/robot/commit/"))
        self.assertIn("/blob/", rec["file_url"])

    def test_json_feed(self):
        feed = json.loads(read(os.path.join(self.out, "feed.json")))
        self.assertEqual(feed["version"], "https://jsonfeed.org/version/1.1")
        item = feed["items"][0]
        self.assertEqual(item["url"], f"https://example.github.io/robot/entries/{self.entry(1)['short']}.html")
        self.assertTrue(any(c["name"] == "LOOP_HZ" for c in item["_knoblog"]["changes"]))
        self.assertEqual(item["_knoblog"]["post"]["status"], "ready")

    def test_atom(self):
        root = ET.parse(os.path.join(self.out, "feed.xml")).getroot()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        self.assertEqual(len(root.findall("a:entry", ns)), 2)

    def test_site_pages(self):
        for p in ("index.html", "params.html", "posts.html", "style.css", f"entries/{self.entry(1)['short']}.html"):
            self.assertTrue(os.path.exists(os.path.join(self.out, p)), p)
        html = read(os.path.join(self.out, "entries", f"{self.entry(1)['short']}.html"))
        self.assertIn("max_vel_x", html)
        self.assertIn("Post text (X-ready, not posted)", html)

    def test_posts(self):
        posts = json.loads(read(os.path.join(self.out, "posts.json")))["posts"]
        p = next(x for x in posts if x["sha"] == self.entry(1)["sha"])
        self.assertEqual(p["status"], "ready")
        text = "\n".join(p["posts"])
        self.assertIn("controller_server/FollowPath/max_vel_x 0.26 → 0.5 (92% higher)", text)
        self.assertIn("LOOP_HZ 400 → 800 (2x higher)", text)
        self.assertIn("https://example.github.io/robot/entries/", text)
        self.assertTrue(all(n <= 280 for n in p["weighted_lengths"]))
        root = next(x for x in posts if x["sha"] == self.entry(0)["sha"])
        self.assertEqual(root["status"], "skip")
        self.assertIn("initial commit", root["reason"])


if __name__ == "__main__":
    unittest.main()
