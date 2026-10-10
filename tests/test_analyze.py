import unittest

from knoblog.analyze import _refine_keyed, same_value


def c(kind, name, old, new, file="p.yaml", type_="number"):
    return {"extractor": "ros2_yaml", "file": file, "name": name, "keyed": True, "kind": kind, "old": old, "new": new, "type": type_}


class Refine(unittest.TestCase):
    def test_same_value(self):
        self.assertTrue(same_value("0.5", "0.50"))
        self.assertTrue(same_value("True", "true"))
        self.assertTrue(same_value("['a']", '["a"]'))
        self.assertFalse(same_value("1", "1.0"))  # int -> double matters for ROS 2 parameter types
        self.assertFalse(same_value("[1, 2]", "[1, 3]"))

    def test_list_split(self):
        out = _refine_keyed([c("changed", "vs.max_accel", "[2.5, 0.0, 3.2]", "[3.0, 0.0, 3.2]", type_="list")], set())
        self.assertEqual([(x["name"], x["old"], x["new"]) for x in out], [("vs.max_accel[0]", "2.5", "3.0")])
        self.assertAlmostEqual(out[0]["pct"], 20.0)

    def test_section_rename(self):
        rem = [c("removed", f"dwb_controller.p{i}", str(i), None) for i in range(4)]
        add = [c("added", f"nav2_controller.p{i}", None, str(i)) for i in range(4)]
        out = _refine_keyed(rem + add, set())
        self.assertEqual({x["kind"] for x in out}, {"renamed"})
        self.assertEqual(out[0]["old_name"], "dwb_controller.p0")

    def test_deleted_file_flag(self):
        out = _refine_keyed([c("removed", "a.b", "1", None, file="gone.yaml")], {"gone.yaml"})
        self.assertTrue(out[0]["file_deleted"])


if __name__ == "__main__":
    unittest.main()
