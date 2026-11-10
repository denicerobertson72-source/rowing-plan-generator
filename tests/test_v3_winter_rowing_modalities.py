"""Winter/off-water V3 contracts: rowing purpose is not on-water-only."""
from __future__ import annotations

from datetime import date
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.materialization_capabilities import mode_for_date, minimum_materializable_reservation, rowing_modalities_for_date
from rowing_plan.materialization_v2 import materialize_v2_session
from rowing_plan.models import V2SessionMaterializationRequest
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.session_selection import ROLE_BAND, candidates


AS_OF = date(2026, 11, 16)
CONFIG = json.load(open("config/defaults.json"))
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _profile(environment: str, modes: tuple[str, ...] = ("on_water", "erg")) -> dict:
    return {
        "athlete": {
            "current_approx_weekly_rowing_minutes": 180,
            "experience_level": "intermediate",
            "training_environment": environment,
        },
        "season": {"start_date": "2026-11-01", "end_date": "2026-12-31"},
        "weekly_availability": [
            {"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": list(modes)}
            for day in DAYS
        ],
    }


def _candidate(profile: dict):
    return generate_plan_sequence_v3_candidate(
        profile, CONFIG, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG),
        planning_date=AS_OF,
    )


def test_current_erg_only_environment_overrides_stale_on_water_first_weekday_modes():
    profile = _profile("erg", ("on_water", "erg"))
    assert rowing_modalities_for_date(profile, AS_OF) == ("erg",)
    assert mode_for_date(profile, AS_OF) == "erg"
    assert minimum_materializable_reservation(role="dedicated_ut2", profile=profile, day=AS_OF)[0] is not None


def test_winter_erg_only_plan_preserves_multiple_rowing_specific_exposures_without_cross_training():
    result = _candidate(_profile("erg"))
    assert result.success
    sessions = result.plan["sessions"]
    rowing = [item for item in sessions if item["mode"] in {"erg", "on_water"}]
    assert len([item for item in rowing if AS_OF <= date.fromisoformat(item["date"]) <= AS_OF.replace(day=22)]) >= 3
    assert rowing and {item["mode"] for item in rowing} == {"erg"}
    assert not any(item["mode"] in {"bike", "elliptical", "stair_climber", "running"} for item in sessions)
    load = next(item for item in result.plan["v2_diagnostics"]["training_load_targets"] if item["horizon_days"] == 7)
    endurance = next(item for item in result.plan["v2_diagnostics"]["endurance_demand_targets"] if item["horizon_days"] == 7)
    assert load["total_selected_rowing_minutes"] <= load["maximum_rowing_minutes"]
    assert endurance["total_endurance_target"] == load["target_rowing_minutes"]
    assert endurance["eligible_general_aerobic_residual"] >= 0


def test_mixed_environment_preserves_existing_declared_on_water_preference():
    profile = _profile("mixed", ("on_water", "erg"))
    assert rowing_modalities_for_date(profile, AS_OF) == ("on_water", "erg")
    result = _candidate(profile)
    assert result.success
    assert any(item["mode"] == "on_water" for item in result.plan["sessions"])


def test_erg_quality_catalog_remains_available_while_on_water_skill_authority_stays_distinct():
    profile = _profile("erg")
    for selector_role, phase, experience in (
        ("THRESHOLD", "threshold_development", "intermediate"),
        ("RACE_PACE", "race_specific_preparation", "intermediate"),
        # Existing AN/PP catalog safeguards require experienced athletes and
        # race-specific/taper context; winter erg availability does not relax
        # those purpose and athlete-safety constraints.
        ("ANAEROBIC_CAPACITY", "race_specific_preparation", "experienced"),
        ("SPRINT_POWER", "taper", "experienced"),
    ):
        assert candidates(
            role=selector_role, band=ROLE_BAND[selector_role], experience=experience,
            phase=phase, race_type="general", mode="erg", minutes=90,
        )
    course = materialize_v2_session(
        V2SessionMaterializationRequest("course_practice", AS_OF, 30, "race_specific_preparation", fixed=True, fact={"title": "Course practice"}),
        profile=profile, bands=build_intensity_profile(profile, CONFIG), power=build_power_profile(profile, CONFIG),
    )
    coached = materialize_v2_session(
        V2SessionMaterializationRequest("coached_training", AS_OF, 50, "general_preparation", fixed=True),
        profile=profile, bands=build_intensity_profile(profile, CONFIG), power=build_power_profile(profile, CONFIG),
    )
    assert course.success and course.session["mode"] == "on_water"
    assert coached.success and coached.session["mode"] == "on_water"
