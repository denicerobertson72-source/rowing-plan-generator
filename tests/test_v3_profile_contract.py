"""R1B profile/current-state contracts are pure inputs, not V3 prescriptions."""
from __future__ import annotations

from datetime import date
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.planning_domain import HistoricalFact, HistoricalFactType, Provenance, ProvenanceSource
from rowing_plan.planning_profile import normalize_available_training_capacity, normalize_current_training_state
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.validators import validate_profile
from services.api.tests.disposable_browser_fixture import synthetic_profile


def _profile() -> dict:
    profile = synthetic_profile()
    profile["weekly_availability"] = [
        {"weekday": day, "available": day != "friday", "fixed_rest": day == "sunday", "max_training_minutes": minutes, "rowing_modes": ["erg"]}
        for day, minutes in (("monday", 70), ("tuesday", 70), ("wednesday", 70), ("thursday", 70), ("friday", 70), ("saturday", 90), ("sunday", 90))
    ]
    profile["athlete"].update({
        "current_weekly_training_minutes": 350,
        "current_approx_weekly_rowing_minutes": 240,
        "current_rowing_sessions_per_week": 4,
        "recent_training_consistency": "consistent",
    })
    profile["training_context"] = {
        "current_strength_sessions_per_week": 3,
        "typical_strength_duration_minutes": 45,
        "current_strength_state": "regular",
        "strength_priority": "develop_strength",
    }
    return profile


def _actual(identifier: str, activity_type: str, minutes: int) -> HistoricalFact:
    return HistoricalFact(
        identifier, date(2026, 11, 1), HistoricalFactType.OPTIONAL_ACTIVITY,
        completed_actual={"status": "completed", "activity_type": activity_type, "actual_duration_min": minutes},
        provenance=Provenance(identifier, ProvenanceSource.OPTIONAL_ACTIVITY_LOG),
    )


def test_available_capacity_sums_only_available_non_rest_days_and_keeps_constraints():
    capacity = normalize_available_training_capacity(_profile())
    assert capacity.total_available_minutes == 370  # Friday unavailable; Sunday fixed rest.
    sunday = next(item for item in capacity.days if item.day_key == "sunday")
    assert sunday.fixed_rest and sunday.usable_minutes == 0
    assert next(item for item in capacity.days if item.day_key == "monday").allowed_modalities


def test_legacy_current_fields_normalize_as_evidence_not_a_prescription():
    state = normalize_current_training_state(_profile())
    assert state.source.value == "legacy_profile_estimate"
    assert (state.approximate_total_physical_minutes, state.approximate_rowing_minutes, state.rowing_exposures) == (350, 240, 4)
    assert (state.strength_exposures, state.typical_strength_duration_minutes, state.consistency) == (3, 45, "regular")


def test_recent_actual_precedence_is_explicit_and_not_a_hidden_threshold():
    profile = _profile()
    facts = (_actual("actual:erg", "erg", 50), _actual("actual:strength", "strength", 45))
    mixed = normalize_current_training_state(profile, historical_facts=facts)
    actuals = normalize_current_training_state(profile, historical_facts=facts, recent_actuals_sufficient=True, observation_window_days=14)
    assert mixed.source.value == "mixed"
    assert actuals.source.value == "recent_actuals"
    assert (actuals.approximate_total_physical_minutes, actuals.approximate_rowing_minutes) == (95, 50)
    assert actuals.completed_history_ids == ("actual:erg", "actual:strength")


def test_structured_goals_strength_override_and_combined_preferences_validate_and_persist_shape():
    profile = _profile()
    profile.update({
        "planning_goals": {"primary_goal": "develop_strength", "secondary_goal": "build_endurance", "context_note": "Winter block."},
        "training_preferences": {"combined_days": "preferred", "strength_plus_easy_aerobic": "preferred", "strength_plus_hard_rowing": "avoid", "maximum_combined_days_per_week": 2},
        "current_training_state": {"approximate_total_physical_minutes": 350, "rowing_exposures": 4, "strength_exposures": 3, "typical_strength_duration_minutes": 45, "consistency": "regular"},
    })
    profile["training_context"]["strength_override"] = {"mode": "preferred_target", "sessions_per_week": 3}
    assert validate_profile(profile) == []


def test_invalid_new_profile_preferences_are_rejected_without_changing_legacy_compatibility():
    profile = _profile()
    profile["training_preferences"] = {"combined_days": "always"}
    assert "Training preference combined days is invalid." in validate_profile(profile)


def test_r1b_contract_data_does_not_change_existing_v3_generation():
    base = _profile()
    base["season"] = {"start_date": "2026-11-16", "end_date": "2026-11-22"}
    enriched = {**base, "current_training_state": {"approximate_total_physical_minutes": 350, "consistency": "regular"}, "planning_goals": {"primary_goal": "develop_strength"}, "training_preferences": {"combined_days": "preferred", "strength_plus_easy_aerobic": "preferred", "strength_plus_hard_rowing": "avoid"}}
    config = json.load(open("config/defaults.json"))
    def candidate(profile):
        return generate_plan_sequence_v3_candidate(profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config), planning_date=date(2026, 11, 16))
    before, after = candidate(base), candidate(enriched)
    assert before.success and after.success
    assert before.plan["sessions"] == after.plan["sessions"]
    assert before.plan["calendar_days"] == after.plan["calendar_days"]
