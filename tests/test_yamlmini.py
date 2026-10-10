import unittest

from knoblog import yamlmini

DOC = """# comment
---
amcl:
  ros__parameters:
    alpha1: 0.2
    base_frame_id: "base_footprint"   # trailing comment
    laser_model_type: 'likelihood_field'
    plugins: ["a", "b"]
    nested_flow: {x: 1, y: [2, 3]}
    empty:
    description: |
      line one
      line two
    seq:
      - name: first
        value: 1
      - plain
    critics:
      [
        "A",  # first
        "B",
      ]
    folded:
      a long plain
      scalar
"""


class YamlMini(unittest.TestCase):
    def test_raw_scalars(self):
        d = yamlmini.loads(DOC)["amcl"]["ros__parameters"]
        self.assertEqual(d["alpha1"], "0.2")
        self.assertEqual(d["base_frame_id"], '"base_footprint"')
        self.assertEqual(yamlmini.unquote(d["laser_model_type"]), "likelihood_field")
        self.assertEqual(d["plugins"], ['"a"', '"b"'])
        self.assertEqual(d["nested_flow"], {"x": "1", "y": ["2", "3"]})
        self.assertIsNone(d["empty"])
        self.assertEqual(d["description"], "line one\nline two\n")
        self.assertEqual(d["seq"], [{"name": "first", "value": "1"}, "plain"])
        self.assertEqual(d["critics"], ['"A"', '"B"'])
        self.assertEqual(d["folded"], "a long plain scalar")

    def test_matches_pyyaml_when_available(self):
        try:
            import yaml
        except ModuleNotFoundError:
            self.skipTest("PyYAML not installed")
        ref = yaml.safe_load(DOC)
        mine = yamlmini.to_python(yamlmini.loads(DOC))
        self.assertEqual(mine, ref)

    def test_error(self):
        with self.assertRaises(yamlmini.YamlError):
            yamlmini.loads("a: 1\n   b: 2\n")


if __name__ == "__main__":
    unittest.main()
