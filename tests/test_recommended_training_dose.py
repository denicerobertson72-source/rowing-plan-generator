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
)
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
