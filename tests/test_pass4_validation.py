"""Formal Corrective Pass 4 planner regressions.

These checks intentionally validate the accepted scheduling model; they do not
ask the planner to create cosmetic week-to-week variety.
"""
from __future__ import annotations

import copy
import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.periodization import build_season_phases, build_weekly_training_intents
from rowing_plan.power_profile import build_power_profile
from rowing_plan.schedule_scoring import choose
from rowing_plan.scheduler import _hard_session_spacing, generate_plan
from rowing_plan.session_selection import assign_week_roles
from services.api.tests.disposable_browser_fixture import exported_runtime_profile
from services.api.tests.test_exported_profile_fixture import profile_save_payload


ROOT = Path(__file__).resolve().parents[1]
WEEKS = ("2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28")


def _config():
    return json.loads((ROOT / "config" / "defaults.json").read_text())


def _corrected_disposable_profile():
    """The persisted Profile fixture after its flexible-rest correction."""
    profile = exported_runtime_profile()
    activities = copy.deepcopy(profile["recurring_activities"])
    activities.extend([
        {"activity_id": "private", "activity_type": "private_coaching", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["wednesday"], "preferred_days": [], "allowed_days": [], "prohibited_days": [], "planner_may_choose_day": False},
        {"activity_id": "coach", "activity_type": "coached_row", "sessions_per_week": 1, "scheduling_status": "flexible", "fixed_days": [], "preferred_days": [], "allowed_days": ["tuesday", "thursday"], "prohibited_days": [], "planner_may_choose_day": True},
    ])
    profile = profile_save_payload(profile, activities)
    profile["races"] = [*profile["races"], {"event_name": "Synthetic November A", "start_date": "2026-11-07", "end_date": "2026-11-08", "priority": "A", "race_type": "head_5k"}]
    return profile


def _plan(profile=None):
    profile = profile or _corrected_disposable_profile()
    config = _config()
    return generate_plan(profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config, date(2026, 10, 1)))


def _week(plan, week_start):
    end = (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()
    return [item for item in plan["sessions"] if week_start <= item["date"] <= end]


def _rowing(items):
    return [item for item in items if item.get("rowing_minutes", 0)]


def _independent_rows(items):
    return [item for item in _rowing(items) if item.get("session_id") != "COACHED"]


class Pass4FrequencyAndDisposableValidation(unittest.TestCase):
    def test_corrected_disposable_first_four_normal_weeks_meet_explicit_exposure_targets(self):
        plan = _plan()
        intents = {item["week_start"]: item for item in plan["weekly_training_intents"]}
        observed_roles = set()
        for week_start in WEEKS:
            intent, sessions = intents[week_start], _week(plan, week_start)
            rows, independent = _rowing(sessions), _independent_rows(sessions)
            self.assertEqual(sum(item["session_id"] == "LIFT" for item in sessions), 2)
            self.assertEqual(sum(item["title"] == "Private coaching" for item in sessions), 1)
            self.assertEqual(sum(item["title"] == "Coached row" for item in sessions), 1)
            days = [item for item in plan["calendar_days"] if week_start <= item["date"] <= (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()]
            self.assertEqual(sum(item["state"] == "designated_rest" for item in days), 1)
            self.assertGreaterEqual(len(rows), intent["target_total_rowing_exposures"])
            self.assertEqual(len(rows) - len(independent), intent["target_coached_rowing_exposures"])
            self.assertEqual(len(independent), intent["target_independent_rowing_exposures"])
            observed_roles.update(item.get("session_role") or item["band"] for item in independent)
        self.assertGreater(len(observed_roles), 1)

    def test_coached_rows_count_only_as_total_exposure_and_never_as_known_quality(self):
        plan = _plan()
        intent = next(item for item in plan["weekly_training_intents"] if item["week_start"] == WEEKS[0])
        sessions = _week(plan, WEEKS[0])
        coached = [item for item in _rowing(sessions) if item["session_id"] == "COACHED"]
        independent = _independent_rows(sessions)
        self.assertEqual(len(coached), intent["target_coached_rowing_exposures"])
        self.assertEqual(len(independent), intent["target_independent_rowing_exposures"])
        self.assertTrue(all(item["band"] == "UT2/UT1" for item in coached))
        self.assertFalse(any(item["band"] in {"AT", "TR", "AN", "PP", "RACE"} for item in coached))

    def test_minutes_cannot_mask_an_independent_frequency_shortfall(self):
        profile = _corrected_disposable_profile()
        profile["athlete"]["current_approx_weekly_rowing_minutes"] = 300
        profile["season"]["current_weekly_endurance_minutes"] = 300
        plan = _plan(profile)
        first = next(item for item in plan["weekly_training_intents"] if item["week_start"] == WEEKS[0])
        independent = _independent_rows(_week(plan, WEEKS[0]))
        self.assertEqual(len(independent), first["target_independent_rowing_exposures"])
        self.assertGreaterEqual(first["target_total_rowing_minutes"], 290)

    def test_intermediate_or_experienced_180_minute_athlete_has_two_independent_rows_when_feasible(self):
        profile = _corrected_disposable_profile()
        profile["athlete"].update({"experience_level": "intermediate", "current_approx_weekly_rowing_minutes": 180})
        intent = next(item for item in build_weekly_training_intents(profile, build_season_phases(profile)) if item["week_start"] == WEEKS[0])
        self.assertGreaterEqual(intent["target_independent_rowing_exposures"], 2)

    def test_available_sunday_is_used_for_an_independent_row_when_needed(self):
        plan = _plan()
        sunday = [item for item in _week(plan, WEEKS[0]) if item["day"] == "Sunday" and item.get("rowing_minutes")]
        self.assertEqual(len(sunday), 1)
        self.assertNotEqual(sunday[0]["session_id"], "COACHED")

    def test_sunday_is_not_hard_coded_and_other_valid_days_can_score_better(self):
        result = choose({"sessions_per_week": 1, "scheduling_status": "flexible", "preferred_days": [], "allowed_days": ["tuesday", "thursday", "sunday"], "prohibited_days": []}, {"tuesday"}, set())
        self.assertEqual(result["scheduled_days"], ["thursday"])


class Pass4SafetyRoleAndPlacementValidation(unittest.TestCase):
    def test_novice_does_not_inherit_two_independent_floor_and_remains_technique_first(self):
        profile = _corrected_disposable_profile()
        profile["athlete"].update({"experience_level": "novice", "current_rowing_sessions_per_week": 2, "current_approx_weekly_rowing_minutes": 60, "recent_training_consistency": "building", "longest_comfortable_continuous_row_minutes": 10})
        profile["races"] = []
        plan = _plan(profile)
        first = next(item for item in plan["weekly_training_intents"] if item["week_start"] == WEEKS[0])
        self.assertEqual(first["target_rowing_sessions"], 2)
        self.assertEqual(first["target_independent_rowing_exposures"], 0)
        self.assertEqual(first["primary_session_roles"], ["TECHNIQUE_EASY", "AEROBIC_BASE"])
        self.assertEqual(first["target_total_rowing_minutes"], 100)  # two coach-led rows set the feasible volume floor

    def test_phase_appropriate_independent_roles_are_distinct(self):
        threshold = {"phase_mix": [{"phase_type": "threshold_development"}], "load_direction": "hold"}
        race_build = {"phase_mix": [{"phase_type": "race_specific_preparation"}], "load_direction": "hold"}
        dates = ["2026-09-08", "2026-09-13"]
        self.assertEqual(list(assign_week_roles(dates, threshold, "head_5k").values()), ["THRESHOLD", "LONG_AEROBIC"])
        self.assertEqual(list(assign_week_roles(dates, race_build, "head_5k").values()), ["RACE_PACE", "THRESHOLD"])

    def test_strength_preferences_prohibitions_and_same_day_row_rule_survive(self):
        plan = _plan()
        week = _week(plan, WEEKS[0])
        lifts = [item for item in week if item["session_id"] == "LIFT"]
        self.assertEqual([item["day"] for item in lifts], ["Monday", "Friday"])
        self.assertNotIn("Wednesday", [item["day"] for item in lifts])
        lift_dates = {item["date"] for item in lifts}
        self.assertFalse(any(item["date"] in lift_dates and item.get("rowing_minutes") for item in week if item["session_id"] != "LIFT" and item["mode"] != "treadmill_walk_jog"))

    def test_rest_is_exactly_one_not_saturday_specific_and_not_adjacent_to_an_avoidable_empty_day(self):
        plan = _plan()
        for week_start in WEEKS:
            days = [item for item in plan["calendar_days"] if week_start <= item["date"] <= (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()]
            self.assertEqual(sum(item["state"] == "designated_rest" for item in days), 1)
            rest = next(item for item in days if item["state"] == "designated_rest")
            rest_index = days.index(rest)
            adjacent = [days[index] for index in (rest_index - 1, rest_index + 1) if 0 <= index < len(days)]
            adjacent_dates = {item["date"] for item in adjacent}
            self.assertTrue(any(item.get("rowing_minutes") or item["session_id"] == "LIFT" for item in _week(plan, week_start) if item["date"] in adjacent_dates))

    def test_no_avoidable_adjacent_independent_hard_sessions_and_race_periods_can_reduce_frequency(self):
        plan = _plan()
        self.assertFalse(_hard_session_spacing([item for item in plan["sessions"] if item.get("session_id") != "COACHED"]))
        race_profile = _corrected_disposable_profile()
        race_profile["races"] = [{"event_name": "Near race", "start_date": "2026-09-12", "end_date": "2026-09-12", "priority": "A", "race_type": "head_5k"}]
        race_profile["athlete"]["current_rowing_sessions_per_week"] = 2
        race_plan = _plan(race_profile)
        race_week = next(item for item in race_plan["weekly_training_intents"] if item["week_start"] == WEEKS[0])
        self.assertLess(race_week["target_total_rowing_exposures"], 4)


if __name__ == "__main__":
    unittest.main()
