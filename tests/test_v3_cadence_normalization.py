"""R9B cadence normalization contracts for the replacement V3 spine."""
from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.planning_domain import (
    ComponentAuthority,
    CurrentTrainingSource,
    CurrentTrainingState,
    PhysicalCategory,
    RecommendedDoseSegment,
    RecommendedTrainingDose,
    RowingQualityPlan,
    TrainingAllocation,
    TrainingAllocationSegment,
    TrainingComponentPurpose,
    TrainingModality,
)
from rowing_plan.planning_normalization import normalize_planning_inputs
from rowing_plan.recommended_training_dose import normalize_recommended_dose_cadence
from rowing_plan.replacement_plan_v3 import generate_recommended_training_dose_v3
from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report
from rowing_plan.training_allocation import generate_training_allocation_v3
from rowing_plan.training_days import (
    AuthoritativeComponentFact,
    DailyTrainingCapacity,
    allocation_segment_id,
    generate_training_days_v3,
)


START = date(2026, 11, 18)  # Wednesday; 38 days creates two partial weeks.
END = date(2026, 12, 25)


def _profile() -> dict:
    return {
        "athlete": {
            "current_weekly_training_minutes": 350,
            "current_approx_weekly_rowing_minutes": 180,
            "recent_training_consistency": "consistent",
        },
        "season": {"start_date": START.isoformat(), "end_date": END.isoformat()},
        "training_context": {
            "strength_experience": "advanced",
            "current_strength_state": "regular",
            "current_strength_sessions_per_week": 2,
            "typical_strength_duration_minutes": 45,
            "strength_override": {"mode": "none"},
        },
        "planning_goals": {"primary_goal": "build_endurance"},
        "cross_training": {"modalities": {}},
        "weekly_availability": [
            {"weekday": day, "available": True, "fixed_rest": False,
             "max_training_minutes": 60, "rowing_modes": ["erg"]}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
        "races": [],
    }


def _source_dose() -> RecommendedTrainingDose:
    segment = RecommendedDoseSegment(
        START, END, 1_900, 1_900, 1_900, 2_280, 0, "ordinary", ("test",),
    )
    return RecommendedTrainingDose(
        START, END, (segment,), 1_900, 1_900, 1_900, 2_280, 0,
        "maintenance", "maintain", "moderate", policy_version="test",
    )


def _cadence_allocation() -> TrainingAllocation:
    dose = generate_recommended_training_dose_v3(_profile(), planning_date=START)
    segments = tuple(
        TrainingAllocationSegment(
            item.start_date, item.end_date, 30, 30, 0, 0, 0, 0,
            0, 0, 0, 45, rationale_codes=("test",),
        )
        for item in dose.segments
    )
    return TrainingAllocation(
        START, END, segments, 30 * len(segments), 30 * len(segments), 0, 0, 0, 0,
        rationale_codes=("test",), confidence="high",
    )


def _capacities() -> tuple[DailyTrainingCapacity, ...]:
    return tuple(DailyTrainingCapacity(START + timedelta(days=index), 60) for index in range((END - START).days + 1))


def test_long_r2_segment_subdivides_into_six_calendar_cadence_segments_with_exact_integer_conservation():
    profile = _profile()
    cadence = normalize_recommended_dose_cadence(
        _source_dose(), profile=profile,
        context=normalize_planning_inputs(profile, planning_date=START),
    )

    assert [(item.start_date, item.end_date) for item in cadence.segments] == [
        (date(2026, 11, 18), date(2026, 11, 22)),
        (date(2026, 11, 23), date(2026, 11, 29)),
        (date(2026, 11, 30), date(2026, 12, 6)),
        (date(2026, 12, 7), date(2026, 12, 13)),
        (date(2026, 12, 14), date(2026, 12, 20)),
        (date(2026, 12, 21), date(2026, 12, 25)),
    ]
    assert [item.recommended_physical_minutes for item in cadence.segments] == [250, 350, 350, 350, 350, 250]
    assert [item.available_capacity_minutes for item in cadence.segments] == [300, 420, 420, 420, 420, 300]
    assert cadence.recommended_physical_minutes == 1_900
    assert cadence.available_capacity_minutes == 2_280
    assert all((item.end_date - item.start_date).days < 7 for item in cadence.segments)


def test_non_divisible_integer_minutes_reconcile_deterministically_without_losing_a_minute():
    source = _source_dose()
    segment = RecommendedDoseSegment(
        START, END, 1_901, 1_901, 1_901, 2_280, 0, "ordinary", ("test",),
    )
    non_divisible = RecommendedTrainingDose(
        START, END, (segment,), 1_901, 1_901, 1_901, 2_280, 0,
        source.recommendation_status, source.progression_state, source.confidence,
        policy_version=source.policy_version,
    )
    context = normalize_planning_inputs(_profile(), planning_date=START)
    first = normalize_recommended_dose_cadence(non_divisible, profile=_profile(), context=context)
    second = normalize_recommended_dose_cadence(non_divisible, profile=_profile(), context=context)

    assert first == second
    assert first.recommended_physical_minutes == 1_901
    assert sum(item.recommended_physical_minutes for item in first.segments) == 1_901


def test_v3_r2_entrypoint_uses_cadence_while_one_calendar_week_remains_one_segment():
    long = generate_recommended_training_dose_v3(_profile(), planning_date=START)
    assert len(long.segments) == 6
    one_week = _profile()
    one_week["season"] = {"start_date": "2026-11-23", "end_date": "2026-11-29"}
    assert len(generate_recommended_training_dose_v3(one_week, planning_date=date(2026, 11, 23)).segments) == 1


def test_locked_authority_is_accounted_in_its_actual_cadence_week():
    locked = {
        "date": "2026-12-08", "session_id": "locked-row", "mode": "erg",
        "total_cardio_minutes": 50, "rowing_minutes": 50, "fixed": True,
    }
    dose = generate_recommended_training_dose_v3(
        _profile(), planning_date=START, locked_sessions=(locked,),
    )
    owning = next(item for item in dose.segments if item.start_date <= date(2026, 12, 8) <= item.end_date)
    assert owning.committed_physical_minutes == 50
    assert owning.recommended_physical_minutes >= 50


def test_r3_strength_is_allocated_at_weekly_cadence_not_as_one_long_segment():
    result = generate_training_allocation_v3(_profile(), planning_date=START)
    assert result.success
    allocation = result.allocation
    assert len(allocation.segments) == 6
    exposures = [item.recommended_strength_exposures for item in allocation.segments]
    assert exposures == [1, 2, 2, 2, 2, 1]
    assert all(exposure <= 2 for exposure in exposures)
    assert allocation.recommended_physical_minutes == sum(item.recommended_physical_minutes for item in allocation.segments)


def test_r6_and_r4_consume_the_same_six_cadence_boundaries_end_to_end():
    profile = _profile()
    for entry in profile["weekly_availability"]:
        entry["max_training_minutes"] = 90
    report = generate_v3_diagnostic_report(profile, planning_date=START)
    r3_ranges = tuple((item["start_date"], item["end_date"]) for item in report["r3"]["segments"])
    r6_ranges = tuple((item["segment_start"], item["segment_end"]) for item in report["r6"])

    assert len(r3_ranges) == 6
    assert r6_ranges == r3_ranges
    assert report["conservation"]["pass"]
    segment_ranges = {
        f"r3:{item['start_date'].isoformat()}:{item['end_date'].isoformat()}": (item["start_date"], item["end_date"])
        for item in report["r3"]["segments"]
    }
    for day in report["r4"].days:
        for component in day.components:
            start, end = segment_ranges[component.allocation_segment_id]
            assert start <= day.date <= end


def test_r4_keeps_generated_components_in_owning_cadence_segment_and_preserves_authority(monkeypatch):
    allocation = _cadence_allocation()
    authority_day = date(2026, 12, 8)
    authority = AuthoritativeComponentFact(
        "fixed-row", authority_day, 20, modality=TrainingModality.ERG,
        purpose=TrainingComponentPurpose.EASY_AEROBIC,
    )
    import rowing_plan.training_days as training_days
    original_dates = training_days._dates
    calls = []

    def observed_dates(start, end):
        calls.append((start, end))
        return original_dates(start, end)

    monkeypatch.setattr(training_days, "_dates", observed_dates)
    result = generate_training_days_v3(allocation, _capacities(), (authority,))

    assert result.success
    dated_components = tuple((day.date, component) for day in result.days for component in day.components)
    fixed_date, fixed = next(item for item in dated_components if item[1].source_lineage_id == "authority:fixed-row")
    assert fixed_date == authority_day
    assert fixed.authority is ComponentAuthority.AUTHORITATIVE
    for component_date, component in dated_components:
        segment = next(item for item in allocation.segments if allocation_segment_id(item) == component.allocation_segment_id)
        assert segment.start_date <= component_date <= segment.end_date
    # The one full-horizon calendar construction is expected; every feasibility
    # scan after that is bounded to an owning Monday--Sunday cadence segment.
    assert all(
        (end - start).days < 7
        for start, end in calls
        if (start, end) != (allocation.planning_start, allocation.planning_end)
    )


def test_six_cadence_segments_preserve_r3_rowing_when_r6_has_empty_stale_budgets():
    cadence = _cadence_allocation()
    # Use a materializable ordinary-row duration in every cadence segment.
    # The separate low-duration contract remains responsible for reporting an
    # explicit infeasibility rather than inventing a catalog envelope.
    segments = tuple(
        TrainingAllocationSegment(
            item.start_date, item.end_date, 90, 90, 0, 0, 0, 0,
            0, 0, 0, 45, rationale_codes=("test",),
        )
        for item in cadence.segments
    )
    allocation = TrainingAllocation(
        START, END, segments, 90 * len(segments), 90 * len(segments),
        0, 0, 0, 0, rationale_codes=("test",), confidence="high",
    )
    # R6 is composition-only.  Empty/stale R6 budgets must not erase the
    # physical rowing dose owned by the six R3 cadence segments.
    empty_plans = tuple(
        RowingQualityPlan(
            allocation_segment_id(segment), segment.start_date, segment.end_date,
            0, 0, (), "test", None, "high", policy_version="test",
            performance_profile_version="test", basis_fingerprint=f"empty:{index}",
        )
        for index, segment in enumerate(allocation.segments)
    )

    result = generate_training_days_v3(
        allocation, _capacities(), rowing_quality_plans=empty_plans,
    )

    assert result.success
    for segment in allocation.segments:
        segment_id = allocation_segment_id(segment)
        segment_components = [
            component for day in result.days for component in day.components
            if component.allocation_segment_id == segment_id
        ]
        assert sum(item.duration_minutes for item in segment_components
                   if item.physical_category is PhysicalCategory.ROWING_SPECIFIC) == segment.rowing_specific_minutes
        assert sum(item.duration_minutes for item in segment_components
                   if item.physical_category is PhysicalCategory.STRENGTH) == segment.strength_minutes
        assert sum(item.duration_minutes for item in segment_components) == segment.recommended_physical_minutes
        assert all(segment.start_date <= day.date <= segment.end_date
                   for day in result.days for component in day.components
                   if component.allocation_segment_id == segment_id)


def test_persisted_style_38_day_dose_keeps_all_r3_rowing_when_every_r6_plan_is_empty():
    profile = _profile()
    for item in profile["weekly_availability"]:
        item["max_training_minutes"] = 90
    profile["athlete"].update({
        "current_weekly_training_minutes": 500,
        "current_approx_weekly_rowing_minutes": 240,
    })
    profile["training_context"]["current_strength_sessions_per_week"] = 3
    allocation_result = generate_training_allocation_v3(profile, planning_date=START)
    assert allocation_result.success
    allocation = allocation_result.allocation
    empty_plans = tuple(
        RowingQualityPlan(
            allocation_segment_id(segment), segment.start_date, segment.end_date,
            0, 0, (), "test", None, "high", policy_version="test",
            performance_profile_version="test", basis_fingerprint=f"persisted-empty:{index}",
        )
        for index, segment in enumerate(allocation.segments)
    )
    capacity = tuple(
        DailyTrainingCapacity(START + timedelta(days=index), 90)
        for index in range((END - START).days + 1)
    )

    result = generate_training_days_v3(allocation, capacity, rowing_quality_plans=empty_plans)

    assert result.success
    assert allocation.recommended_physical_minutes == 2_714
    assert sum(day.total_physical_minutes for day in result.days) == allocation.recommended_physical_minutes
    assert sum(component.duration_minutes for component in result.components
               if component.physical_category is PhysicalCategory.ROWING_SPECIFIC) == allocation.rowing_specific_minutes
    assert sum(component.duration_minutes for component in result.components
               if component.physical_category is PhysicalCategory.STRENGTH) == allocation.strength_minutes
