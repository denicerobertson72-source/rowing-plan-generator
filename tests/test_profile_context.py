"""Review-only athlete profile context contracts."""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator

from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_normalization import normalize_planning_inputs
from rowing_plan.training_needs import generate_training_need_plan
from rowing_plan.training_policy import DEFAULT_TRAINING_POLICY
from rowing_plan.validators import validate_profile
from services.api.tests.disposable_browser_fixture import synthetic_profile


AS_OF = date(2026, 9, 1)


def _profile_with_context() -> dict:
    profile = synthetic_profile()
    profile["training_context"] = {
        "strength_experience": "intermediate",
        "current_strength_sessions_per_week": 2,
        "strength_equipment_access": ["full_gym", "dumbbells", "bands_bodyweight"],
        "strength_priority": "support_rowing",
        "strength_limitations": "Avoid overhead barbell work for now.",
        "training_restriction_source": "clinician",
        "training_restriction_summary": "Gradual return; keep sessions under 45 minutes.",
    }
    profile.setdefault("preferences", {})["readiness_checkins_enabled"] = True
    return profile


def test_legacy_profile_without_review_context_remains_valid_and_readiness_is_safely_false():
    profile = synthetic_profile()
    assert "training_context" not in profile
    assert profile.get("preferences", {}).get("readiness_checkins_enabled", False) is False
    assert validate_profile(profile) == []


def test_schema_reflects_existing_ui_fields_and_optional_review_context():
    schema = json.loads(Path("schemas/athlete_profile.schema.json").read_text())
    athlete_schema = schema["properties"]["athlete"]
    context_schema = schema["properties"]["training_context"]
    preferences_schema = schema["properties"]["preferences"]

    Draft202012Validator(athlete_schema).validate({
        "display_name": "Rower", "age_band": "30-39", "experience_level": "intermediate",
        "birth_year": 1979, "training_environment": "mixed",
    })
    Draft202012Validator(context_schema).validate(_profile_with_context()["training_context"])
    Draft202012Validator(preferences_schema).validate({
        "terminology": "UT", "erg_primary_display": "both", "broken_aerobic_preferred": True,
        "readiness_checkins_enabled": False,
    })


def test_review_context_validation_rejects_invalid_strength_frequency_but_not_omission():
    profile = _profile_with_context()
    profile["training_context"]["current_strength_sessions_per_week"] = 8
    assert "Current strength sessions per week must be a whole number from 0 to 7." in validate_profile(profile)

    profile["training_context"].pop("current_strength_sessions_per_week")
    assert validate_profile(profile) == []


def test_v3_normalized_inputs_and_need_generation_ignore_review_only_context():
    base = synthetic_profile()
    review_only = _profile_with_context()
    first_context = normalize_planning_inputs(base, planning_date=AS_OF)
    second_context = normalize_planning_inputs(review_only, planning_date=AS_OF)

    assert first_context == second_context
    first_state = reduce_physiological_state(first_context, profile=base)
    second_state = reduce_physiological_state(second_context, profile=review_only)
    assert first_state == second_state
    assert generate_training_need_plan(first_context, first_state, DEFAULT_TRAINING_POLICY, profile=base) == generate_training_need_plan(second_context, second_state, DEFAULT_TRAINING_POLICY, profile=review_only)


def test_existing_private_check_ins_are_separate_from_profile_context():
    profile = _profile_with_context()
    profile_before = copy.deepcopy(profile)
    private_check_in = {"sleep_impact": "high", "fatigue_impact": "moderate", "symptom_impact": "mild"}

    assert private_check_in not in profile.values()
    assert profile == profile_before
