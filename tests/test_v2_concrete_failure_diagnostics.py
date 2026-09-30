"""Structured diagnostics at the V2 concrete-workout boundary."""
import json
from datetime import date
import pytest

from rowing_plan.models import ConcreteQualitySequenceResult, DateContext, DatedTrainingRole, TrainingDoseTarget, V2SessionMaterializationRequest
from rowing_plan.plan_assembly_v2 import build_plan_version_from_v2
from rowing_plan.planner_v2 import V2PlanningError, _quality_requests, generate_plan_v2
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.periodization import build_season_phases
from rowing_plan.scheduler_v2 import _reservation_policy, build_v2_season_calendar, generate_rowing_dose_targets, generate_v2_demand_plan, solve_v2_rolling_non_rowing
from services.api.tests.disposable_browser_fixture import synthetic_profile


CONFIG = json.load(open("config/defaults.json"))


def test_quality_finalization_failure_identifies_the_exact_concrete_placement(monkeypatch):
    role = DatedTrainingRole(date(2026, 9, 12), "quality", 60, "taper", "quality-source", "provisional", (), "quality-placement")
    monkeypatch.setattr(
        "rowing_plan.planner_v2.finalize_translated_quality_sequence",
        lambda *_args, **_kwargs: ConcreteQualitySequenceResult(
            False,
            failed_placement_id="quality-placement",
            failed_date=date(2026, 9, 12),
            failed_quality_type="TR",
            failure_reason="malformed_concrete_prescription",
            failure_stage="finalization",
        ),
    )
    with pytest.raises(V2PlanningError) as failure:
        _quality_requests(synthetic_profile(), (role,), {"taper": "taper"})
    assert failure.value.reason_code == "v2_concrete_workout_failed"
    assert failure.value.diagnostics == {
        "failure_stage": "quality_finalization",
        "failure_origin": "finalization",
        "placement_id": "quality-placement",
        "date": "2026-09-12",
        "role": "quality",
        "quality_type": "TR",
        "reason_code": "malformed_concrete_prescription",
    }


def test_non_quality_materialization_failure_carries_request_identity():
    profile = synthetic_profile()
    day = date(2026, 9, 7)
    calendar = (DateContext(day, "Monday", "base", True, 90, 0, 90),)
    result = build_plan_version_from_v2(
        profile=profile,
        bands=[],
        power={},
        calendar=calendar,
        requests=(V2SessionMaterializationRequest("long_aerobic", day, 1, "base", placement_id="long-1"),),
    )
    assert not result.success and result.failure_reason == "no_eligible_materialization_archetype"
    assert result.failure_diagnostics == {
        "failure_stage": "plan_assembly",
        "failure_origin": "materialization",
        "placement_id": "long-1",
        "date": "2026-09-07",
        "role": "long_aerobic",
        "quality_type": None,
        "reason_code": "no_eligible_materialization_archetype",
    }


def test_threshold_quality_dose_uses_catalog_reservation_without_inflating_target_credit():
    """AT uses a viable 35-minute envelope while retaining 30-minute dose credit."""
    profile = synthetic_profile()
    profile["season"].update({"start_date": "2026-09-05", "end_date": "2026-09-18"})
    profile["races"] = [{"event_name": "Threshold target", "start_date": "2026-10-12", "end_date": "2026-10-12", "priority": "A", "race_type": "head_5k"}]
    targets = generate_rowing_dose_targets(profile)
    threshold_quality = next(item for item in targets if item.category == "quality" and item.quality_class == "quality")
    assert (threshold_quality.target_exposures, threshold_quality.target_minutes) == (2, 60)
    plan = generate_plan_v2(profile, CONFIG, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG))
    quality = [item for item in plan["sessions"] if item["band"] == "AT"]
    assert len(quality) == 2 and all(item["total_cardio_minutes"] >= 35 for item in quality)
    target = next(item for window in plan["v2_diagnostics"]["rolling_targets"] for item in window["rowing_targets"] if item["category"] == "quality")
    assert target["target_minutes"] == 60 and target["achieved_minutes"] == 60
    assert {key: target[key] for key in ("exposure_target", "nominal_minutes_per_exposure", "reservation_minutes_per_exposure", "total_reserved_minutes", "target_credit_minutes", "catalog_envelope_overshoot_minutes", "reason_code")} == {"exposure_target": 2, "nominal_minutes_per_exposure": 30, "reservation_minutes_per_exposure": 35, "total_reserved_minutes": 70, "target_credit_minutes": 30, "catalog_envelope_overshoot_minutes": 5, "reason_code": "catalog_minimum_session_envelope"}
    phases = build_season_phases(profile)
    calendar = build_v2_season_calendar(profile, phases)
    state, _ = solve_v2_rolling_non_rowing(profile, generate_v2_demand_plan(profile, phases), calendar)
    placements = [item for item in (*state.frozen_placements, *state.provisional_placements) if item.role == "quality"]
    assert len(placements) == 2
    for placement in placements:
        original = next(item.remaining_minutes for item in calendar if item.date == placement.date)
        assert placement.minutes == 35
        assert placement.credits[0].minutes == 30
        assert state.remaining_minutes_by_date[placement.date] == original - 35


@pytest.mark.parametrize(
    ("category", "phase_type", "nominal_minutes", "expected_reservation"),
    [
        ("dedicated_ut2", "threshold_development", 40, 40),
        ("long_aerobic", "threshold_development", 60, 60),
        ("ut1_aerobic_strength", "threshold_development", 45, 45),
        ("quality", "race_specific_preparation", 25, 25),
        ("quality", "taper", 30, 30),
        ("quality", "anaerobic_development", 20, 20),
        ("quality", "sprint_power", 20, 20),
    ],
)
def test_catalog_reservation_adapter_does_not_change_other_current_or_future_role_envelopes(category, phase_type, nominal_minutes, expected_reservation):
    target = TrainingDoseTarget("phase", category, date(2026, 9, 7), date(2026, 9, 20), 14, 1, 1, nominal_minutes, nominal_minutes, "quality" if category == "quality" else "aerobic", phase_type=phase_type)
    policy = _reservation_policy(synthetic_profile(), target, nominal_minutes)
    assert policy["reservation_minutes_per_exposure"] == expected_reservation
    assert policy["target_credit_minutes"] == nominal_minutes
