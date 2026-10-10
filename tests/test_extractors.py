import unittest

from knoblog.extractors import auto_extractors, parse_num, run_extractor


def vals(d):
    return {k: v["value"] for k, v in d.items()}


class ParseNum(unittest.TestCase):
    def test_literals(self):
        cases = {"0.4": 0.4, "-43.2": -43.2, "125_000": 125000, "1.0f64": 1.0, "0.95f": 0.95, "(10)": 10, "1e-3": 1e-3,
                 "0x10": 16, "10UL": 10, "1'000": 1000, "1.0e-10": 1e-10}
        for raw, want in cases.items():
            self.assertEqual(parse_num(raw), want, raw)

    def test_non_numbers(self):
        for raw in ("abc", "Duration::from_secs(1)", "", None, True, "1.2.3"):
            self.assertIsNone(parse_num(raw), raw)


class Extractors(unittest.TestCase):
    def test_rust_param_and_const(self):
        src = '''
param!(FavoriteWeight, f64, "home_mixer.fav_weight", 0.5);
param!(EnableThing, bool, "home_mixer.enable_thing", true);
pub const MAX_RESULTS: usize = 1_000;
const TIMEOUT: Duration = Duration::from_millis(150);
'''
        p = run_extractor("rust_param", "a.rs", src)
        self.assertEqual(vals(p), {"FavoriteWeight": "0.5", "EnableThing": "true"})
        self.assertEqual(p["FavoriteWeight"]["decl_type"], "f64")
        self.assertEqual(p["FavoriteWeight"]["key"], "home_mixer.fav_weight")
        c = vals(run_extractor("rust_const", "a.rs", src))
        self.assertEqual(c["MAX_RESULTS"], "1_000")
        self.assertEqual(c["TIMEOUT"], "Duration::from_millis(150)")
        self.assertEqual(auto_extractors("a.rs", src), ["rust_param", "rust_const"])

    def test_python_const(self):
        src = "MAX_SPEED = 1.5\nKP: float = 0.8  # gain\ndef f():\n    local = 3\nNAME = 'x'\n"
        v = vals(run_extractor("python_const", "a.py", src))
        self.assertEqual(v["MAX_SPEED"], "1.5")
        self.assertEqual(v["KP"], "0.8")
        self.assertNotIn("local", v)

    def test_c_const(self):
        src = """
#define MAX_RPM 12000
#define KP_ROLL (0.15f)
constexpr double kMaxVel = 2.5;
static const int LOOP_HZ{400};
#define STR "x"
"""
        v = vals(run_extractor("c_const", "a.hpp", src))
        self.assertEqual(v["MAX_RPM"], "12000")
        self.assertEqual(parse_num(v["KP_ROLL"]), 0.15)
        self.assertEqual(v["kMaxVel"], "2.5")
        self.assertEqual(v["LOOP_HZ"], "400")

    def test_px4_param_define(self):
        src = "PARAM_DEFINE_FLOAT(MC_ROLLRATE_P, 0.15f);\nPARAM_DEFINE_INT32(MC_AIRMODE, 0);\n"
        v = vals(run_extractor("px4_param_define", "mc_params.c", src))
        self.assertEqual(parse_num(v["MC_ROLLRATE_P"]), 0.15)
        self.assertEqual(v["MC_AIRMODE"], "0")
        self.assertIn("px4_param_define", auto_extractors("mc_params.c", src))

    def test_px4_module_yaml(self):
        src = """module_name: mc_rate_control
parameters:
  - group: Multicopter Rate Control
    definitions:
      MC_ROLLRATE_P:
        description:
          short: Roll rate P gain
        type: float
        default: 0.15
        min: 0.01
      MC_ACRO_EXPO:
        type: float
        default: 0.69
"""
        v = vals(run_extractor("px4_module_yaml", "src/modules/mc_rate_control/module.yaml", src))
        self.assertEqual(v, {"MC_ROLLRATE_P": "0.15", "MC_ACRO_EXPO": "0.69"})
        self.assertEqual(auto_extractors("module.yaml", src), ["px4_module_yaml"])

    def test_ardupilot(self):
        src = """
const AP_Param::GroupInfo AC_PosControl::var_info[] = {
    AP_GROUPINFO("_POSZ_P", 2, AC_PosControl, _p_pos_z, POSCONTROL_POS_Z_P),
    AP_GROUPINFO_FLAGS("_ENABLE", 0, AC_PosControl, _enable, 1, AP_PARAM_FLAG_ENABLE),
    AP_GROUPINFO("_ACC_XY", 3, AC_PosControl, _accel_xy, 2.5f),
    AP_GROUPEND
};
GSCALAR(throttle_filt, "PILOT_THR_FILT", 0),
"""
        src = "#define LOITER_DEFAULT_OPTIONS  0  // off\n" + src.replace("2.5f)", "LOITER_DEFAULT_OPTIONS)")
        v = vals(run_extractor("ardupilot", "AC_PosControl.cpp", src))
        self.assertEqual(v["AC_PosControl._ENABLE"], "1")
        d = run_extractor("ardupilot", "AC_PosControl.cpp", src)
        self.assertEqual((d["AC_PosControl._ACC_XY"]["value"], d["AC_PosControl._ACC_XY"]["macro"]), ("0", "LOITER_DEFAULT_OPTIONS"))
        self.assertEqual(v["AC_PosControl._POSZ_P"], "POSCONTROL_POS_Z_P")
        self.assertEqual(v["PILOT_THR_FILT"], "0")

    def test_ros2_yaml(self):
        src = """controller_server:
  ros__parameters:
    controller_frequency: 20.0
    FollowPath:
      plugin: "dwb_core::DWBLocalPlanner"
      critics:
        [
          "RotateToGoal",
          "Oscillation",
        ]
      max_vel_x: 0.26  # m/s
      debug: True
velocity_smoother:
  ros__parameters:
    max_accel: [2.5, 0.0, 3.2]
"""
        d = run_extractor("ros2_yaml", "nav2_params.yaml", src)
        v = vals(d)
        self.assertEqual(v["controller_server.controller_frequency"], "20.0")
        self.assertEqual(v["controller_server.FollowPath.plugin"], '"dwb_core::DWBLocalPlanner"')
        self.assertEqual(v["controller_server.FollowPath.max_vel_x"], "0.26")
        self.assertEqual(v["controller_server.FollowPath.critics"], '["RotateToGoal", "Oscillation"]')
        self.assertEqual(v["velocity_smoother.max_accel"], "[2.5, 0.0, 3.2]")
        self.assertEqual(d["controller_server.FollowPath.debug"]["type"], "bool")
        self.assertEqual(auto_extractors("x.yaml", src), ["ros2_yaml"])

    def test_json_toml(self):
        v = vals(run_extractor("json", "a.json", '{"pid": {"kp": 1.5, "ki": 0}, "on": true, "list": [1, 2]}'))
        self.assertEqual(v, {"pid.kp": "1.5", "pid.ki": "0", "on": "true", "list": "[1, 2]"})
        v = vals(run_extractor("toml", "a.toml", '[pid]\nkp = 1.5\nname = "x"\n'))
        self.assertEqual(v["pid.kp"], "1.5")

    def test_duplicates_dropped_and_bad_input(self):
        self.assertEqual(run_extractor("python_const", "a.py", "X = 1\nX = 2\n"), {})
        self.assertEqual(run_extractor("json", "a.json", "{not json"), {})
        self.assertEqual(run_extractor("yaml", "a.yaml", "a: [unterminated\n  b: : :"), {} if True else None)


if __name__ == "__main__":
    unittest.main()
