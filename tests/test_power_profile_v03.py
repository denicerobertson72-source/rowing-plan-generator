import json
import unittest
from datetime import date
from pathlib import Path

from rowing_plan.power_profile import build_power_profile, profile_test_rejection_reason

ROOT = Path(__file__).parents[1]

class WinterPowerProfileTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT / "config/defaults.json").read_text())
        self.profile={"tests":{"testing_blocks":[{"id":"a","label":"A","end_date":"2026-08-01","performance_tests":[
            {"id":"2k","protocol":"two_k","test_date":"2026-08-01","average_watts":200,"time_seconds":500,"valid_for_profile":True,"source":"manual"},
            {"id":"peak","protocol":"seven_stroke_peak","test_date":"2026-08-01","peak_watts":400,"valid_for_profile":True,"source":"manual"},
            {"id":"60","protocol":"sixty_second","test_date":"2026-08-01","average_watts":300,"valid_for_profile":True,"source":"manual"}
        ]}]}}
    def test_required_ratios_use_actual_2k(self):
        power=build_power_profile(self.profile,self.config,date(2026,8,9))
        self.assertEqual(power["metrics"]["peak_to_2k_ratio"],2.0)
        self.assertEqual(power["metrics"]["sixty_to_2k_ratio"],1.5)
        self.assertEqual(power["metrics"]["sixty_to_peak_ratio"],.75)
        self.assertEqual(power["two_k_watts"],200)
    def test_no_2k_does_not_invent_one(self):
        tests=self.profile["tests"]["testing_blocks"][0]["performance_tests"]
        self.profile["tests"]["testing_blocks"][0]["performance_tests"]=[x for x in tests if x["protocol"] != "two_k"]
        power=build_power_profile(self.profile,self.config,date(2026,8,9))
        self.assertIsNone(power["metrics"]["peak_to_2k_ratio"])
        self.assertIsNone(power["two_k_watts"])
        self.assertEqual(power["metrics"]["sixty_to_peak_ratio"],.75)
    def test_no_universal_60_second_prediction(self):
        code=(ROOT / "rowing_plan/power_profile.py").read_text()
        self.assertNotIn("1.531",code)
        self.assertNotIn("predicted_2k",code)

    def test_explicit_profile_approval_overrides_generic_test_age_default(self):
        test=self.profile["tests"]["testing_blocks"][0]["performance_tests"][0]
        test["test_date"]="2025-01-01"
        self.assertIsNone(profile_test_rejection_reason(test, date(2026, 8, 9), 180))
        test.pop("valid_for_profile")
        self.assertIn("older than", profile_test_rejection_reason(test, date(2026, 8, 9), 180) or "")

if __name__ == "__main__": unittest.main()
