"""R3B allocation contracts: an exact partition of the authoritative R2 dose."""
from __future__ import annotations

from datetime import date, timedelta
import inspect

import pytest

from rowing_plan.planning_domain import (
    CurrentTrainingSource,
    CurrentTrainingState,
    PhysiologicalState,
    RecommendedDoseSegment,
    RecommendedTrainingDose,
    TrainingAllocationSegment,
)
from rowing_plan.training_allocation import (
    allocate_training_dose,
    generate_training_allocation_v3,
    weekly_evidence_for_allocation,
)


START = date(2026, 11, 16)


def _dose(total: int, *, days: int = 7) -> RecommendedTrainingDose:
    end = START + timedelta(days=days - 1)
    segment = RecommendedDoseSegment(
        START, end, total, total, total, 1_000, 0, "ordinary", ("test",),
    )
    return RecommendedTrainingDose(
        START, end, (segment,), total, total, total, 1_000, 0,
        "maintenance", "maintain", "moderate", policy_version="test",
    )


def _profile(*, goal="develop_strength", cross=True, override=None, rowing=240, strength_sessions=3, phase="general_preparation") -> dict:
    modes = {"elliptical": {"enabled": cross, "familiarity": "regular", "preference": "neutral"}}
    profile = {
        "athlete": {"current_approx_weekly_rowing_minutes": rowing, "current_weekly_training_minutes": 380},
        "season": {"start_date": START.isoformat(), "end_date": "2026-12-31", "phase_type": phase},
        "training_context": {
            "strength_experience": "advanced" if strength_sessions else "none",
            "current_strength_state": "regular" if strength_sessions else "none",
            "current_strength_sessions_per_week": strength_sessions,
            "typical_strength_duration_minutes": 45,
            "strength_override": override or {"mode": "none"},
        },
        "planning_goals": {"primary_goal": goal},
        "cross_training": {"modalities": modes},
        "weekly_availability": [
            {"weekday": day, "available": True, "fixed_rest": False, "max_training_minutes": 120}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
        "races": [],
    }
    return profile


def _state(strength=3, rowing=240) -> CurrentTrainingState:
    return CurrentTrainingState(
        CurrentTrainingSource.ATHLETE_ESTIMATE,
        approximate_total_physical_minutes=380,
        approximate_rowing_minutes=rowing,
        strength_exposures=strength,
        typical_strength_duration_minutes=45,
        consistency="regular",
        confidence="moderate",
    )


def _allocate(profile, dose):
    return allocate_training_dose(dose, profile=profile, current_state=_state(
        profile["training_context"]["current_strength_sessions_per_week"],
        profile["athlete"]["current_approx_weekly_rowing_minutes"],
    ), state=PhysiologicalState(as_of_date=START))


def test_conservation_and_block_totals_are_exactly_derived_from_segments():
    result = _allocate(_profile(), _dose(380))
    assert result.success
    allocation = result.allocation
    assert allocation.recommended_physical_minutes == 380
    assert allocation.rowing_specific_minutes + allocation.strength_minutes + allocation.general_aerobic_minutes + allocation.other_physical_minutes == 380
    assert allocation.recommended_physical_minutes == sum(item.recommended_physical_minutes for item in allocation.segments)


def test_early_winter_strength_goal_keeps_strength_inside_same_r2_dose():
    result = _allocate(_profile(), _dose(380))
    segment = result.allocation.segments[0]
    assert (segment.recommended_strength_exposures, segment.strength_minutes) == (3, 135)
    assert segment.general_aerobic_minutes == 30
    assert segment.rowing_specific_minutes >= segment.rowing_specific_floor_minutes


def test_race_specific_same_total_shifts_toward_rowing_and_drops_discretionary_general_aerobic():
    winter = _allocate(_profile(), _dose(380)).allocation
    profile = _profile(phase="race_specific_preparation")
    profile["races"] = [{"race_dates": ["2026-11-22"]}]
    race = _allocate(profile, _dose(380)).allocation
    assert race.general_aerobic_minutes == 0
    assert race.rowing_specific_minutes > winter.rowing_specific_minutes
    assert race.recommended_physical_minutes == winter.recommended_physical_minutes


def test_no_strength_experience_does_not_receive_three_hard_lifts():
    result = _allocate(_profile(goal="build_endurance", strength_sessions=0), _dose(380))
    assert result.success
    assert result.allocation.recommended_strength_exposures == 0


def test_regular_experienced_athlete_never_silently_gets_zero_strength():
    result = _allocate(_profile(), _dose(380))
    segment = result.allocation.segments[0]
    assert segment.recommended_strength_exposures > 0 or any(code.startswith("strength_zero:") for code in segment.rationale_codes)


def test_fixed_strength_three_by_45_is_135_minutes():
    profile = _profile(override={"mode": "fixed_commitment", "sessions_per_week": 3, "expected_duration_minutes": 45})
    result = _allocate(profile, _dose(380))
    assert result.success
    segment = result.allocation.segments[0]
    assert (segment.recommended_strength_exposures, segment.expected_strength_duration_minutes, segment.strength_minutes) == (3, 45, 135)


def test_hard_strength_and_rowing_floor_conflict_returns_explicit_infeasibility():
    profile = _profile(
        goal="build_endurance", cross=False, rowing=180,
        override={"mode": "minimum", "sessions_per_week": 3, "expected_duration_minutes": 60},
    )
    result = _allocate(profile, _dose(300))
    assert not result.success
    failure = result.infeasibility
    assert failure.code == "allocation_infeasible"
    assert (failure.required_strength_minutes, failure.rowing_specific_floor_minutes, failure.recommended_physical_minutes, failure.shortfall_minutes) == (180, 180, 300, 60)


def test_no_cross_training_preference_permits_zero_general_aerobic():
    result = _allocate(_profile(cross=False), _dose(380))
    assert result.success
    assert result.allocation.general_aerobic_minutes == 0


def test_partial_segment_uses_a_discrete_not_full_week_strength_recommendation():
    result = _allocate(_profile(), _dose(163, days=3))
    segment = result.allocation.segments[0]
    assert segment.recommended_strength_exposures == 1
    assert segment.strength_minutes == 45


def test_same_total_different_goals_changes_allocation_without_creating_minutes():
    strength = _allocate(_profile(goal="develop_strength"), _dose(380)).allocation
    endurance = _allocate(_profile(goal="build_endurance"), _dose(380)).allocation
    assert strength != endurance
    assert strength.recommended_physical_minutes == endurance.recommended_physical_minutes == 380


def test_other_cannot_absorb_an_unexplained_residual():
    with pytest.raises(ValueError, match="other_requires_provenance"):
        TrainingAllocationSegment(
            START, START, 60, 50, 0, 0, 10, 0, 0, 0, 0, 45,
        )


def test_entrypoint_does_not_import_or_call_legacy_future_dose_generators():
    source = inspect.getsource(generate_training_allocation_v3)
    assert "derive_training_load_targets" not in source
    assert "derive_total_training_load_targets" not in source
    assert "derive_endurance_demand_targets" not in source


def test_r3_entrypoint_consumes_r2_directly_and_returns_an_allocation():
    result = generate_training_allocation_v3(_profile(), planning_date=START)
    assert result.success
    assert result.allocation.recommended_physical_minutes == sum(
        item.recommended_physical_minutes for item in result.allocation.segments
    )


def test_identical_inputs_produce_identical_allocation():
    profile, dose = _profile(), _dose(380)
    assert _allocate(profile, dose) == _allocate(profile, dose)


def _observation_profile(*, explicit_strength=None, explicit_rowing=None):
    profile = _profile(goal="build_endurance", strength_sessions=0, rowing=0)
    context = profile["training_context"]
    athlete = profile["athlete"]
    context.pop("current_strength_sessions_per_week", None)
    athlete.pop("current_approx_weekly_rowing_minutes", None)
    if explicit_strength is not None:
        context["current_strength_sessions_per_week"] = explicit_strength
    if explicit_rowing is not None:
        athlete["current_approx_weekly_rowing_minutes"] = explicit_rowing
    return profile


def _observed_state(*, window=21, rowing=240, strength=3):
    return CurrentTrainingState(
        CurrentTrainingSource.RECENT_ACTUALS,
        observation_window_days=window,
        approximate_rowing_minutes=rowing,
        strength_exposures=strength,
        typical_strength_duration_minutes=45,
        consistency="regular",
        confidence="high",
    )


def test_observation_window_evidence_is_normalized_to_a_conservative_weekly_rate_with_provenance():
    evidence = weekly_evidence_for_allocation(_observation_profile(), _observed_state())
    assert evidence == {
        "observation_window_days": 21,
        "raw_observation_rowing_minutes": 240,
        "raw_observation_strength_exposures": 3,
        "weekly_rowing_minutes": 80,
        "weekly_strength_exposures": 1,
        "rowing_evidence_source": "normalized_observation_window_evidence",
        "strength_evidence_source": "normalized_observation_window_evidence",
    }


def test_explicit_weekly_profile_evidence_wins_over_observed_window_totals_and_is_not_divided_again():
    evidence = weekly_evidence_for_allocation(
        _observation_profile(explicit_strength=3, explicit_rowing=240), _observed_state(),
    )
    assert evidence["weekly_strength_exposures"] == 3
    assert evidence["weekly_rowing_minutes"] == 240
    assert evidence["strength_evidence_source"] == "explicit_weekly_profile_context"
    assert evidence["rowing_evidence_source"] == "explicit_weekly_profile_context"


def test_seven_day_observation_window_rates_are_unchanged():
    evidence = weekly_evidence_for_allocation(_observation_profile(), _observed_state(window=7))
    assert (evidence["weekly_rowing_minutes"], evidence["weekly_strength_exposures"]) == (240, 3)


def test_partial_cadence_segment_scales_normalized_weekly_rowing_floor_without_full_week_strength():
    profile = _observation_profile()
    state = _observed_state()
    result = allocate_training_dose(_dose(112, days=3), profile=profile, current_state=state, state=PhysiologicalState(as_of_date=START))
    segment = result.allocation.segments[0]
    assert segment.rowing_specific_floor_minutes == 34  # round(80 minutes/week * 3 / 7)
    assert segment.recommended_strength_exposures == 0
    assert "rowing_weekly_evidence:normalized_observation_window_evidence" in segment.rationale_codes
    assert "strength_weekly_evidence:normalized_observation_window_evidence" in segment.rationale_codes


def test_persisted_style_21_day_evidence_does_not_zero_strength_across_a_38_day_cadence_block():
    profile = _observation_profile()
    start = date(2026, 11, 18)
    end = date(2026, 12, 25)
    # The cadence contract is supplied by R2B/R9B; using six short source
    # segments here isolates R3's observed-window interpretation.
    segments = []
    cursor = start
    for minutes, days in ((250, 5), (350, 7), (350, 7), (350, 7), (350, 7), (250, 5)):
        finish = cursor + timedelta(days=days - 1)
        segments.append(RecommendedDoseSegment(cursor, finish, minutes, minutes, minutes, days * 60, 0, "ordinary", ("cadence",)))
        cursor = finish + timedelta(days=1)
    cadence = RecommendedTrainingDose(
        start, end, tuple(segments), 1_900, 1_900, 1_900, 2_280, 0,
        "maintenance", "maintain", "high", policy_version="test",
    )
    result = allocate_training_dose(cadence, profile=profile, current_state=_observed_state(), state=PhysiologicalState(as_of_date=start))
    assert result.success
    assert [item.recommended_strength_exposures for item in result.allocation.segments] == [0, 1, 1, 1, 1, 0]
    assert all("strength_reduced_for_rowing_specific_protection" not in item.rationale_codes for item in result.allocation.segments)
    assert result.allocation.recommended_physical_minutes == 1_900
