"""R6 pure composition contracts; no R4 calendar or live planner route."""
from __future__ import annotations

from datetime import date
import inspect

import pytest

from rowing_plan.performance_profile import (
    build_athlete_performance_profile, derive_performance_limiter,
    derive_training_focus,
)
from rowing_plan.planning_domain import RowingQualityFamily, TrainingAllocationSegment
from rowing_plan.rowing_quality_v3 import (
    CoachQualityOverride, CompletedQualityExposure, RowingQualityPlanningContext,
    generate_rowing_quality_plan, rowing_quality_plan_planversion_fragment,
)


DAY = date(2026, 11, 2)


def _test(identifier: str, protocol: str, watts: int) -> dict[str, object]:
    return {"id": identifier, "protocol": protocol, "average_watts": watts,
            "test_date": "2026-10-25", "valid_for_profile": True}


def _profile(tests=()):
    return {"tests": {"testing_blocks": [{"id": "battery", "performance_tests": list(tests)}]}}


def _segment(minutes: int = 200) -> TrainingAllocationSegment:
    return TrainingAllocationSegment(DAY, date(2026, 11, 15), minutes, minutes, 0, 0, 0, 0, 0, 0, 0, 45)


def _result(tests=(), *, context=None, completed=(), override=None, phase="general_preparation", segment=None):
    performance = build_athlete_performance_profile(_profile(tests), as_of=DAY)
    limiter = derive_performance_limiter(performance)
    context = context or RowingQualityPlanningContext(DAY, phase_id=phase)
    focus = derive_training_focus(limiter, phase=context.phase_id, race_type=context.race_type,
                                  days_to_race=context.days_to_race, recovery_active=context.recovery_active)
    return generate_rowing_quality_plan(
        segment or _segment(), allocation_segment_id="seg:0", performance=performance, limiter=limiter,
        focus=focus, context=context, completed_exposures=completed, coach_override=override,
    )


def _family(result):
    assert result.plan is not None
    return result.plan.requirements[0].family if result.plan.requirements else None


@pytest.mark.parametrize(("tests", "expected_mode", "expected_family"), [
    ((), "unknown", RowingQualityFamily.AEROBIC_STRENGTH),
    ((_test("2k", "two_k", 300),), "integrated_anchor", RowingQualityFamily.AEROBIC_STRENGTH),
    ((_test("r20", "thirty_min_rate_capped_optional", 150),), "component_context", RowingQualityFamily.AEROBIC_STRENGTH),
    ((_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 150)), "athlete_relative_heuristic", RowingQualityFamily.AEROBIC_STRENGTH),
    ((_test("2k", "two_k", 300), _test("60", "sixty_second", 180)), "athlete_relative_heuristic", RowingQualityFamily.THRESHOLD),
    ((_test("2k", "two_k", 300), _test("7", "seven_stroke_peak", 200)), "athlete_relative_heuristic", RowingQualityFamily.AEROBIC_STRENGTH),
])
def test_optional_tests_degrade_conservatively_without_changing_r3_rowing_budget(tests, expected_mode, expected_family):
    result = _result(tests, phase="threshold_development" if expected_family is RowingQualityFamily.THRESHOLD else "general_preparation")
    assert result.plan and result.success
    assert result.plan.rowing_specific_minutes == 200
    assert _family(result) is expected_family
    performance = build_athlete_performance_profile(_profile(tests), as_of=DAY)
    assert derive_performance_limiter(performance).mode == expected_mode


def test_full_battery_and_same_r3_budget_can_change_composition_only():
    aerobic = _result((_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 140), _test("60", "sixty_second", 430), _test("7", "seven_stroke_peak", 650)))
    anaerobic = _result((_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 420), _test("60", "sixty_second", 160), _test("7", "seven_stroke_peak", 650)), phase="threshold_development")
    assert aerobic.plan and anaerobic.plan
    assert aerobic.plan.rowing_specific_minutes == anaerobic.plan.rowing_specific_minutes == 200
    assert _family(aerobic) is RowingQualityFamily.AEROBIC_STRENGTH
    assert _family(anaerobic) is RowingQualityFamily.THRESHOLD


def test_same_r3_budget_with_different_limiter_changes_one_k_specific_composition():
    context = RowingQualityPlanningContext(DAY, phase_id="race_specific_preparation", race_type="one_k", experience="experienced")
    aerobic = _result((_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 140)), context=context)
    peak = _result((_test("2k", "two_k", 300), _test("7", "seven_stroke_peak", 120)), context=context)
    assert aerobic.plan and peak.plan and aerobic.success and peak.success
    assert aerobic.plan.rowing_specific_minutes == peak.plan.rowing_specific_minutes == 200
    assert _family(aerobic) is RowingQualityFamily.RACE_SPECIFIC
    assert _family(peak) is RowingQualityFamily.POWER


@pytest.mark.parametrize("race_type", ["one_k", "head_5k"])
def test_race_context_and_taper_take_precedence_over_long_term_limiter(race_type):
    result = _result(
        (_test("2k", "two_k", 300), _test("7", "seven_stroke_peak", 120)),
        context=RowingQualityPlanningContext(DAY, phase_id="taper", race_type=race_type, days_to_race=4),
    )
    assert result.plan and result.success
    assert _family(result) is RowingQualityFamily.RACE_SPECIFIC
    assert result.plan.low_intensity_guard_minutes == 100


def test_completed_quality_suppresses_duplicate_but_uncompleted_does_not():
    tests = (_test("2k", "two_k", 300), _test("60", "sixty_second", 160))
    completed = _result(tests, phase="threshold_development", completed=(CompletedQualityExposure(DAY, "AT", 20),))
    planned_only = _result(tests, phase="threshold_development", completed=(CompletedQualityExposure(DAY, "AT", 20, completed=False),))
    assert completed.plan and completed.success and completed.plan.requirements == ()
    assert _family(planned_only) is RowingQualityFamily.THRESHOLD


def test_pp_history_suppression_and_technical_emphasis_do_not_add_physical_dose():
    peak_context = RowingQualityPlanningContext(DAY, phase_id="power_development")
    peak = _result((_test("2k", "two_k", 300), _test("7", "seven_stroke_peak", 120)), context=peak_context,
                   completed=(CompletedQualityExposure(DAY, "PP", 10),))
    technical = _result((), context=RowingQualityPlanningContext(DAY, phase_id="general_preparation", goals=("technical_development",)))
    assert peak.plan and peak.plan.requirements == ()
    assert technical.plan and technical.plan.technical_emphasis is True
    assert technical.plan.rowing_specific_minutes == 200


def test_multi_race_cluster_is_one_contextual_requirement_not_multiple_physical_doses():
    result = _result((), context=RowingQualityPlanningContext(DAY, phase_id="race_specific_preparation", race_type="head_5k", days_to_race=5, race_count=3))
    assert result.plan and result.success
    assert _family(result) is RowingQualityFamily.RACE_SPECIFIC
    assert len(result.plan.requirements) == 1 and result.plan.rowing_specific_minutes == 200


def test_same_athlete_early_winter_and_specific_phase_change_requirement_not_r3_budget():
    tests = (_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 140))
    winter = _result(tests)
    specific = _result(tests, context=RowingQualityPlanningContext(DAY, phase_id="race_specific_preparation", race_type="head_5k", experience="experienced"))
    assert winter.plan and specific.plan and winter.success and specific.success
    assert _family(winter) is RowingQualityFamily.AEROBIC_STRENGTH
    assert _family(specific) is RowingQualityFamily.RACE_SPECIFIC
    assert winter.plan.rowing_specific_minutes == specific.plan.rowing_specific_minutes == 200


def test_recent_pp_suppresses_pp_and_an_incompatible_catalog_is_explicit():
    context = RowingQualityPlanningContext(DAY, phase_id="race_specific_preparation", race_type="one_k", experience="experienced")
    tests = (_test("2k", "two_k", 300), _test("7", "seven_stroke_peak", 120))
    suppressed = _result(tests, context=context, completed=(CompletedQualityExposure(DAY, "PP", 10),))
    assert suppressed.plan and suppressed.success and suppressed.plan.requirements == ()
    short = _result(tests, context=context, segment=_segment(15))
    assert short.plan and not short.success
    assert short.infeasibilities[0].code == "quality_envelope_exceeds_r3_rowing_budget"


def test_coach_override_changes_composition_not_r3_and_hard_restriction_is_explicit():
    override = CoachQualityOverride(RowingQualityFamily.POWER, desired_work_minutes=10)
    regular = _result((), context=RowingQualityPlanningContext(DAY, phase_id="race_specific_preparation", race_type="one_k", experience="experienced"), override=override)
    blocked = _result((), context=RowingQualityPlanningContext(DAY, hard_restriction_codes=("post_race_recovery",)), override=override)
    assert regular.plan and regular.success and _family(regular) is RowingQualityFamily.POWER
    assert regular.plan.rowing_specific_minutes == 200
    assert blocked.plan and not blocked.success
    assert blocked.infeasibilities[0].code == "coach_override_conflicts_with_hard_restriction"


def test_every_requirement_has_r5_capability_and_persistence_is_additive_and_deterministic():
    first = _result((_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 140)))
    second = _result((_test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 140)))
    assert first == second and first.plan and first.success
    requirement = first.plan.requirements[0]
    assert requirement.compatible_envelopes
    assert all(minimum > 0 and maximum >= minimum for _identifier, minimum, maximum in requirement.compatible_envelopes)
    fragment = rowing_quality_plan_planversion_fragment((first.plan,))
    assert fragment["rowing_quality_plan_version"] == "rowing-quality-plan-v1"
    assert fragment["rowing_quality_plans"][0]["rowing_specific_minutes"] == 200


def test_r6_does_not_use_legacy_dose_or_need_authorities():
    import rowing_plan.rowing_quality_v3 as subject
    source = inspect.getsource(subject)
    assert "TrainingLoad" not in source
    assert "TrainingNeed" not in source
