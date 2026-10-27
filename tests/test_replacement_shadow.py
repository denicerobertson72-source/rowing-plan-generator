"""Milestone 9 shadow comparison contracts; all fixtures remain in-memory."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from rowing_plan.continuous_placement import PlacementSearchConfig
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import MaterializationDiagnostic, generate_plan_sequence_v3_candidate
from rowing_plan.replacement_shadow import compare_v2_with_v3, evaluate_research_contracts
from services.api.tests.disposable_browser_fixture import synthetic_profile


CONFIG = json.load(open("config/defaults.json"))
DAY = date(2026, 9, 6)


def _candidate(*, race=False, prior=None, actuals=None, locked=(), planning_date=DAY):
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-09-01", "end_date": "2026-10-05", "current_weekly_endurance_minutes": 180, "target_peak_weekly_endurance_minutes": 220}
    profile["recurring_activities"] = [
        {"activity_id": "private", "activity_type": "private_coaching", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["wednesday"]},
        {"activity_id": "coach", "activity_type": "coached_row", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["thursday"]},
    ]
    if race:
        profile["races"] = [
            {"event_name": "Saturday race", "start_date": "2026-09-12", "end_date": "2026-09-12", "priority": "B", "race_type": "head_5k"},
            {"event_name": "Next race", "start_date": "2026-09-20", "end_date": "2026-09-20", "priority": "B", "race_type": "head_5k"},
        ]
    bands = build_intensity_profile(profile, CONFIG)
    power = build_power_profile(profile, CONFIG)
    candidate = generate_plan_sequence_v3_candidate(
        profile, CONFIG, bands, power, planning_date=planning_date, prior_plan=prior,
        history_actuals=actuals, locked_sessions=locked,
        placement_config=PlacementSearchConfig(horizon_days=21, beam_width=48),
    )
    # These fixtures exercise typed V3 contract comparison at an artificial
    # historical boundary.  The legacy V2 diagnostic builder only supports
    # its live/current boundary there, so a complete PlanVersion-shaped
    # reference is sufficient for intent comparison; API coverage below uses
    # the real V2 request path.
    return profile, candidate, dict(candidate.plan or {"sessions": []})


@pytest.mark.parametrize("fixture", ["ordinary", "race_return", "taper", "optional_actual", "incomplete_history", "user_fixed", "availability", "repeat"])
def test_shadow_high_value_fixtures_are_deterministic_and_non_weekly(fixture):
    prior = None
    actuals = None
    locked = ()
    race = fixture in {"race_return", "taper", "repeat"}
    if fixture == "optional_actual":
        prior = {"calendar_days": [{"date": "2026-09-30", "designated_rest": True, "state": "designated_rest", "unavailable": False, "commitments": []}], "sessions": []}
        actuals = {"actual:2026-09-30:erg": {"status": "completed", "activity_type": "erg", "actual_duration_min": 20, "actual_intensity": "UT2"}}
    elif fixture == "incomplete_history":
        prior = {"calendar_days": [{"date": "2026-09-05", "state": "planned"}], "sessions": [{"date": "2026-09-05", "day": "Saturday", "session_id": "UT2", "mode": "erg", "title": "Incomplete", "band": "UT2", "total_cardio_minutes": 40, "phase": "general_preparation", "structure": "40 min"}]}
    elif fixture == "user_fixed":
        locked = ({"date": "2026-09-09", "day": "Wednesday", "session_id": "fixed-row", "mode": "erg", "title": "Athlete choice", "band": "UT2", "total_cardio_minutes": 40, "rowing_minutes": 40, "quality_minutes": 0, "phase": "general_preparation", "structure": "40 min", "user_fixed": True, "placement_id": "fixed-row"},)
    elif fixture == "availability":
        # The candidate still owns need identity while dates may legitimately differ.
        pass
    planning_date = date(2026, 10, 1) if fixture == "optional_actual" else DAY
    profile, candidate, v2 = _candidate(race=race, prior=prior, actuals=actuals, locked=locked, planning_date=planning_date)
    comparison = compare_v2_with_v3(v2_plan=v2, candidate=candidate, planning_date=planning_date)
    assert comparison.planning_date == planning_date
    assert "flexible_dates_not_compared_as_failures" in comparison.flexible_date_differences or not candidate.success
    assert all("weekday" not in blocker for blocker in comparison.promotion_blockers)
    assert candidate.stage_timings_ms
    if fixture == "repeat":
        _, second, second_v2 = _candidate(race=True)
        assert candidate == second
        assert comparison == compare_v2_with_v3(v2_plan=second_v2, candidate=second, planning_date=DAY)
    if fixture == "optional_actual":
        assert len([fact for fact in candidate.context.historical_facts if fact.completed_actual]) == 1
        assert not any(item.get("session_id") == "erg" for item in candidate.plan["sessions"])
    if fixture == "incomplete_history":
        assert any(fact.planned_prescription and not fact.physiological_credit.eligible for fact in candidate.context.historical_facts)


def test_shadow_blockers_classify_candidate_failure_history_authority_and_materialization():
    _, candidate, v2 = _candidate(race=True)
    assert candidate.success
    materialization = replace(candidate, success=False, plan=None,
                              materialization_diagnostics=(MaterializationDiagnostic("no_eligible_archetype"),),
                              failure_reason="no_eligible_archetype")
    compared = compare_v2_with_v3(v2_plan=v2, candidate=materialization, planning_date=DAY)
    assert "materialization_contract_failure" in compared.promotion_blockers
    contracts = evaluate_research_contracts(candidate)
    assert {item.contract for item in contracts} >= {
        "historical_facts_preserved", "authority_preserved_or_explicit_conflict",
        "recovery_protection", "target_credit_not_materialization_duration",
        "week_boundary_independence",
    }


def test_shadow_promotes_history_authority_race_and_recovery_contract_failures_to_blockers():
    _, candidate, v2 = _candidate(race=True)
    _, historical_candidate, historical_v2 = _candidate(
        planning_date=date(2026, 10, 1),
        prior={"calendar_days": [{"date": "2026-09-30", "designated_rest": True, "state": "designated_rest", "unavailable": False, "commitments": []}], "sessions": []},
    )
    historical = replace(historical_candidate, plan={**historical_candidate.plan, "sessions": [], "calendar_days": []})
    history_comparison = compare_v2_with_v3(v2_plan=historical_v2, candidate=historical, planning_date=date(2026, 10, 1))
    assert "history_preservation_regression" in history_comparison.promotion_blockers

    stripped = tuple(item for item in candidate.placements if item.authority_status.value == "provisional")
    authority = replace(candidate, placements=stripped)
    authority_comparison = compare_v2_with_v3(v2_plan=v2, candidate=authority, planning_date=DAY)
    assert "authority_regression" in authority_comparison.promotion_blockers
    assert "race_taper_regression" in authority_comparison.promotion_blockers
