"""Regression coverage for the sanitized exported Profile payload."""
from __future__ import annotations

import copy
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from rowing_plan.power_profile import _valid, build_power_profile, testing_blocks
from services.api.app.repositories import SQLiteRepositories
from services.api.tests.disposable_browser_fixture import exported_runtime_profile

ROOT = Path(__file__).resolve().parents[3]


def profile_save_payload(profile: dict, activities: list[dict]) -> dict:
    """The Profile activity-save payload after modern rest normalization."""
    payload = copy.deepcopy(profile)
    normalized = copy.deepcopy(activities)
    fixed_rest_days: list[str] = []
    for activity in normalized:
        if activity.get("activity_type") != "rest":
            continue
        if activity.get("scheduling_status") != "fixed":
            activity["fixed_days"] = []
            activity["planner_may_choose_day"] = activity.get("scheduling_status") == "flexible"
            if activity.get("scheduling_status") == "flexible" and not activity.get("allowed_days"):
                activity["allowed_days"] = [entry["weekday"] for entry in payload.get("weekly_availability", []) if entry.get("available", True)]
        else:
            fixed_rest_days.extend(activity.get("fixed_days", []))
            activity["planner_may_choose_day"] = False
    payload["recurring_activities"] = normalized
    payload.setdefault("preferences", {})["fixed_rest_weekdays"] = fixed_rest_days
    for entry in payload.get("weekly_availability", []):
        entry["fixed_rest"] = entry.get("weekday") in fixed_rest_days
    return payload


class ExportedProfileFixtureTests(unittest.TestCase):
    def setUp(self):
        self.profile = exported_runtime_profile()

    def test_power_profile_reads_testing_blocks_before_legacy_empty_collection(self):
        config = __import__("json").loads((ROOT / "config" / "defaults.json").read_text())
        block = testing_blocks(self.profile)[0]
        sources = {item["protocol"]: item for item in block["performance_tests"]}
        self.assertEqual(sources["two_k"]["time_seconds"], 496)
        self.assertEqual(sources["sixty_second"]["average_watts"], 220)
        self.assertEqual(sources["seven_stroke_peak"]["average_watts"], 287)
        self.assertTrue(all(_valid(item, date(2026, 10, 1), 365) for item in sources.values()))
        power = build_power_profile(self.profile, config, date(2026, 10, 1))
        self.assertEqual(power["status"], "2k_plus_peak_plus_60s")
        self.assertIsNotNone(power["two_k_watts"])
        self.assertEqual(power["longitudinal"]["current"]["watts"]["sixty_second"], 220)
        self.assertEqual(power["longitudinal"]["current"]["watts"]["seven_stroke_peak"], 287)
        self.assertIsNotNone(power["metrics"]["peak_to_2k_ratio"])

    def test_activity_and_race_saves_are_lossless_after_rest_becomes_flexible(self):
        activities = copy.deepcopy(self.profile["recurring_activities"])
        activities.append({"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]})
        activities.append({"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[],"planner_may_choose_day":True})
        payload = profile_save_payload(self.profile, activities)
        payload["races"] = [*payload["races"], {"event_name":"Synthetic November A","start_date":"2026-11-07","end_date":"2026-11-08","priority":"A"}]
        with TemporaryDirectory() as directory:
            repo = SQLiteRepositories(Path(directory) / "fixture.sqlite3")
            athlete_id = repo.create(payload, "fixture-user")
            reloaded = repo.get(athlete_id)
        self.assertEqual([race["event_name"] for race in reloaded["races"]], ["HOCR", "Speakmon", "Synthetic November A"])
        by_type = {activity["activity_type"]: activity for activity in reloaded["recurring_activities"]}
        rest, strength, private, coached = by_type["rest"], by_type["strength"], by_type["private_coaching"], by_type["coached_row"]
        self.assertEqual(rest["sessions_per_week"], 1)
        self.assertEqual(rest["scheduling_status"], "flexible")
        self.assertEqual(rest["fixed_days"], [])
        self.assertEqual(rest["allowed_days"], ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])
        self.assertTrue(rest["planner_may_choose_day"])
        self.assertEqual(reloaded["preferences"]["fixed_rest_weekdays"], [])
        self.assertFalse(next(day for day in reloaded["weekly_availability"] if day["weekday"] == "saturday")["fixed_rest"])
        self.assertEqual(strength["same_day_rules"], {"rowing_allowed": False, "alternate_ut2_allowed": True})
        self.assertEqual(strength["preferred_days"], ["monday", "friday"])
        self.assertEqual(strength["allowed_days"], ["monday", "friday", "tuesday", "thursday"])
        self.assertEqual(private["fixed_days"], ["wednesday"])
        self.assertEqual(coached["allowed_days"], ["tuesday", "thursday"])
        self.assertTrue(coached["planner_may_choose_day"])
        self.assertEqual(reloaded["tests"], self.profile["tests"])


if __name__ == "__main__":
    unittest.main()
