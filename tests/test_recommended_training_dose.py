"""R2B contracts for the single authoritative future physical-dose source."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from rowing_plan.planning_domain import HistoricalFact, HistoricalFactType, Provenance, ProvenanceSource
from rowing_plan.planning_normalization import normalize_planning_inputs
from rowing_plan.recommended_training_dose import (
    RecommendedDoseInfeasibleError,
    derive_recommended_training_dose,
    recent_actual_history_is_sufficient,
    r2_baseline_evidence,
)
from rowing_plan.planning_profile import normalize_current_training_state
from rowing_plan.replacement_plan_v3 import generate_recommended_training_dose_v3
from rowing_plan.validators import validate_profile
from services.api.tests.disposable_browser_fixture import synthetic_profile


def _profile() -> dict:
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-16", "end_date": "2026-11-29"}
    profile["weekly_availability"] = [
        {"weekday": day, "available": True, "fixed_rest": False, "max_training_minutes": minutes, "rowing_modes": ["erg"]}
        for day, minutes in (("monday", 70), ("tuesday", 70), ("wednesday", 70), ("thursday", 70), ("friday", 70), ("saturday", 90), ("sunday", 90))
    ]
    profile["athlete"].update({
        "experience_level": "experienced",
        "current_weekly_training_minutes": 350,
        "recent_training_consistency": "consistent",
    })
    return profile


def _context(profile: dict, start: date, *, history_actuals=None):
    return normalize_planning_inputs(profile, planning_date=start, history_actuals=history_actuals or {})


def _actual_fact(identifier: str, day: date, minutes: int) -> HistoricalFact:
    return HistoricalFact(
        identifier, day, HistoricalFactType.OPTIONAL_ACTIVITY,
        completed_actual={"status": "completed", "activity_type": "erg", "actual_duration_min": minutes},
        provenance=Provenance(identifier, ProvenanceSource.OPTIONAL_ACTIVITY_LOG),
    )


def _baseline(profile: dict, facts: tuple[HistoricalFact, ...], start: date) -> dict:
    current = normalize_current_training_state(
        profile, historical_facts=facts,
        recent_actuals_sufficient=recent_actual_history_is_sufficient(facts, planning_start=start),
        observation_window_days=21,
    )
    return r2_baseline_evidence(profile, current, facts, planning_start=start)


def test_block_totals_are_derived_from_non_overlapping_segments_and_capacity_is_not_filled():
    profile = _profile()
    start, end = date(2026, 11, 16), date(2026, 11, 29)
    dose = derive_recommended_training_dose(profile, _context(profile, start), planning_start=start, planning_end=end)

    assert dose.available_capacity_minutes == 1_060
    assert dose.recommended_physical_minutes == 700  # current evidence, not 1,060 capacity
    assert dose.recommended_physical_minutes == sum(item.recommended_physical_minutes for item in dose.segments)
    assert dose.available_capacity_minutes == sum(item.available_capacity_minutes for item in dose.segments)
    assert dose.committed_physical_minutes == sum(item.committed_physical_minutes for item in dose.segments)
    assert dose.segments[0].start_date == start and dose.segments[-1].end_date == end


def test_partial_terminal_segment_uses_actual_days_not_a_copied_full_week():
    profile = _profile()
    start, end = date(2026, 11, 16), date(2026, 11, 18)
    dose = derive_recommended_training_dose(profile, _context(profile, start), planning_start=start, planning_end=end)

    assert dose.recommended_physical_minutes == 150
    assert dose.segments == (dose.segments[0],)
    assert dose.segments[0].recommended_physical_minutes == 150


def test_fixed_override_that_exceeds_usable_capacity_is_a_deterministic_conflict():
    profile = _profile()
    profile["planning_dose_override"] = {"fixed_physical_minutes": 1_061}
    start, end = date(2026, 11, 16), date(2026, 11, 29)

    with pytest.raises(RecommendedDoseInfeasibleError, match="fixed_dose_exceeds_usable_capacity"):
        derive_recommended_training_dose(profile, _context(profile, start), planning_start=start, planning_end=end)


def test_fixed_override_is_honoured_exactly_when_feasible_and_remains_derived():
    profile = _profile()
    profile["planning_dose_override"] = {"fixed_physical_minutes": 600}
    start, end = date(2026, 11, 16), date(2026, 11, 29)
    dose = derive_recommended_training_dose(profile, _context(profile, start), planning_start=start, planning_end=end)

    assert dose.recommended_physical_minutes == 600
    assert dose.recommendation_status == "override_fixed"
    assert all(item.recommended_physical_minutes <= item.available_capacity_minutes for item in dose.segments)


def test_sufficient_history_requires_multiple_dates_weeks_and_observable_time():
    start = date(2026, 11, 22)
    facts = tuple(
        HistoricalFact(
            id=f"actual:{offset}", date=start - timedelta(days=20 - offset * 3),
            type=HistoricalFactType.OPTIONAL_ACTIVITY,
            completed_actual={"activity_type": "erg", "actual_duration_min": 45},
            provenance=Provenance(f"actual:{offset}", ProvenanceSource.OPTIONAL_ACTIVITY_LOG),
        )
        for offset in range(6)
    )
    assert recent_actual_history_is_sufficient(facts, planning_start=start)
    assert not recent_actual_history_is_sufficient(facts[:5], planning_start=start)


def test_v3_r2_entrypoint_has_no_legacy_target_or_placement_dependency():
    profile = _profile()
    dose = generate_recommended_training_dose_v3(profile, planning_date=date(2026, 11, 16))

    assert dose.policy_version == "recommended-dose-v1"
    assert dose.planning_start == date(2026, 11, 16)
    assert dose.planning_end == date(2026, 11, 29)
    assert dose.recommended_physical_minutes == 700


def test_dose_override_schema_accepts_one_value_and_rejects_ambiguous_values():
    profile = _profile()
    profile["planning_dose_override"] = {"fixed_physical_minutes": 500}
    assert validate_profile(profile) == []
    profile["planning_dose_override"] = {
        "fixed_physical_minutes": 500,
        "preferred_physical_minutes": 450,
    }
    assert "Planning dose override must provide exactly one non-negative physical-minute value." in validate_profile(profile)


def test_insufficient_sparse_actuals_do_not_dilute_an_explicit_weekly_total_estimate():
    profile = _profile()
    profile["athlete"]["current_weekly_training_minutes"] = 500
    start = date(2026, 11, 16)
    facts = tuple(_actual_fact(f"actual:{offset}", start - timedelta(days=20 - offset * 4), 60) for offset in range(4))
    context = _context(profile, start)
    context = type(context)(
        planning_date=context.planning_date, historical_facts=facts,
        authoritative_commitments=context.authoritative_commitments,
        provisional_prior_placement_ids=context.provisional_prior_placement_ids,
        race_context=context.race_context, authority_precedence=context.authority_precedence,
        diagnostics=context.diagnostics,
    )
    dose = derive_recommended_training_dose(profile, context, planning_start=start, planning_end=start + timedelta(days=6))
    evidence = _baseline(profile, facts, start)

    assert evidence["actual_history_sufficient"] is False
    assert evidence["normalized_actual_weekly_minutes"] is not None
    assert evidence["selected_baseline_weekly_minutes"] == 500
    assert evidence["selected_baseline_source"] == "explicit_weekly_total_training_estimate"
    assert evidence["insufficient_actual_history_not_used_for_dose"] is True
    assert dose.recommended_physical_minutes == 500
    assert "baseline_source:explicit_weekly_total_training_estimate" in dose.rationale_codes
    assert "insufficient_actual_history_not_used_for_dose" in dose.rationale_codes


def test_sufficient_actuals_remain_the_r2_baseline_even_when_an_explicit_weekly_total_exists():
    profile = _profile()
    profile["athlete"]["current_weekly_training_minutes"] = 500
    start = date(2026, 11, 16)
    facts = tuple(
        _actual_fact(f"actual:{offset}", start - timedelta(days=days), 100)
        for offset, days in enumerate((20, 16, 12, 8, 4, 1))
    )
    evidence = _baseline(profile, facts, start)

    assert evidence["actual_history_sufficient"] is True
    assert evidence["normalized_actual_weekly_minutes"] == 200
    assert evidence["selected_baseline_weekly_minutes"] == 200
    assert evidence["selected_baseline_source"] == "sufficient_recent_actual_training"


def test_insufficient_actuals_without_explicit_weekly_total_use_documented_conservative_fallback():
    profile = _profile()
    profile["athlete"].pop("current_weekly_training_minutes", None)
    start = date(2026, 11, 16)
    facts = tuple(_actual_fact(f"actual:{offset}", start - timedelta(days=20 - offset * 4), 60) for offset in range(4))
    evidence = _baseline(profile, facts, start)

    assert evidence["actual_history_sufficient"] is False
    assert evidence["selected_baseline_source"] == "fallback_conservative_experience_baseline"
    assert evidence["insufficient_actual_history_not_used_for_dose"] is True


def test_explicit_weekly_total_without_actuals_and_seven_day_actual_observation_keep_their_expected_rates():
    profile = _profile()
    profile["athlete"]["current_weekly_training_minutes"] = 500
    start = date(2026, 11, 16)
    explicit = _baseline(profile, (), start)
    one_week = tuple(
        _actual_fact(f"actual:{offset}", start - timedelta(days=days), 50)
        for offset, days in enumerate((6, 5, 4, 3, 2, 1, 1))
    )
    profile["athlete"].pop("current_weekly_training_minutes", None)
    actual = _baseline(profile, one_week, start)

    assert (explicit["selected_baseline_weekly_minutes"], explicit["selected_baseline_source"]) == (500, "explicit_weekly_total_training_estimate")
    assert actual["normalized_actual_weekly_minutes"] == 350
