from datetime import date, timedelta

from rowing_plan.planning_domain import (
    ComponentAuthority,
    GeneralAerobicModalityCapability,
    PhysicalCategory,
    RaceEvent,
    TrainingAllocation,
    TrainingAllocationSegment,
    TrainingComponentPurpose,
    TrainingDayState,
    TrainingModality,
)
from rowing_plan.training_days import (
    AuthoritativeComponentFact,
    CombinedDayPreferences,
    DailyTrainingCapacity,
    component_calendar_planversion_fragment,
    generate_training_days_v3,
    stable_component_identity,
)


START = date(2026, 1, 5)


def allocation(*, days=1, rowing=0, strength_exposures=0, strength_duration=45,
               general=0, other=0, other_provenance=(), general_capabilities=None):
    end = START + timedelta(days=days - 1)
    strength = strength_exposures * strength_duration
    total = rowing + strength + general + other
    segment = TrainingAllocationSegment(
        START, end, total, rowing, strength, general, other, 0,
        strength_exposures, 0, strength_exposures, strength_duration,
        rationale_codes=("test",), other_provenance=other_provenance,
        general_aerobic_capabilities=(
            (GeneralAerobicModalityCapability(TrainingModality.STAIR_CLIMBER, "regular", "neutral", ("test",)),)
            if general and general_capabilities is None else tuple(general_capabilities or ())
        ),
    )
    return TrainingAllocation(
        START, end, (segment,), total, rowing, strength, general, other,
        strength_exposures, rationale_codes=("test",), confidence="high",
    )


def capacities(*minutes, rest=(), unavailable=()):
    return tuple(
        DailyTrainingCapacity(START + timedelta(days=index), value,
                              available=index not in unavailable,
                              fixed_rest=index in rest)
        for index, value in enumerate(minutes)
    )


def category_minutes(result, category):
    return sum(component.duration_minutes for component in result.components if component.physical_category is category)


def test_strength_only_and_rowing_only_days_are_exact_components():
    strength = generate_training_days_v3(allocation(strength_exposures=1), capacities(45))
    row = generate_training_days_v3(allocation(rowing=50), capacities(50))
    assert strength.success and row.success
    assert [(item.physical_category, item.duration_minutes) for item in strength.components] == [(PhysicalCategory.STRENGTH, 45)]
    assert [(item.physical_category, item.duration_minutes) for item in row.components] == [(PhysicalCategory.ROWING_SPECIFIC, 50)]


def test_combined_strength_general_and_easy_rowing_days_preserve_independent_components():
    general = generate_training_days_v3(allocation(strength_exposures=1, general=15), capacities(60))
    rowing = generate_training_days_v3(allocation(strength_exposures=1, rowing=20), capacities(65))
    assert general.success and rowing.success
    assert [item.physical_category for item in general.days[0].components] == [PhysicalCategory.STRENGTH, PhysicalCategory.GENERAL_AEROBIC]
    assert [item.physical_category for item in rowing.days[0].components] == [PhysicalCategory.STRENGTH, PhysicalCategory.ROWING_SPECIFIC]
    assert general.days[0].combined_day and general.days[0].total_physical_minutes == 60
    assert rowing.days[0].combined_day and rowing.days[0].total_physical_minutes == 65


def test_generated_capacity_overflow_is_deterministic_and_never_overfills_day():
    result = generate_training_days_v3(allocation(strength_exposures=1, rowing=20), capacities(60))
    assert not result.success
    assert any(item.code == "generated_component_capacity_infeasible" for item in result.infeasibilities)
    assert all(day.total_physical_minutes <= day.available_minutes for day in result.days)


def test_authoritative_over_capacity_is_preserved_and_blocks_generated_additions():
    coached = AuthoritativeComponentFact("coached-1", START, 75, modality=TrainingModality.ERG,
                                         purpose=TrainingComponentPurpose.COACHED)
    result = generate_training_days_v3(allocation(rowing=75), capacities(60), (coached,))
    assert result.success
    assert result.days[0].authoritative_capacity_conflict
    assert result.days[0].components[0].duration_minutes == 75
    assert any(item.code == "authoritative_capacity_conflict" for item in result.diagnostics)


def test_combined_day_preferences_and_calendar_week_maximum_are_represented():
    value = allocation(days=3, strength_exposures=1, general=15)
    avoid = generate_training_days_v3(value, capacities(60, 15, 0), preferences=CombinedDayPreferences(combined_days="avoid"))
    preferred = generate_training_days_v3(value, capacities(60, 15, 0), preferences=CombinedDayPreferences(combined_days="preferred"))
    limited = generate_training_days_v3(value, capacities(60, 15, 15), preferences=CombinedDayPreferences(maximum_combined_days_per_week=0))
    assert avoid.success and preferred.success and limited.success
    assert not avoid.days[0].combined_day and avoid.days[1].total_physical_minutes == 15
    assert preferred.days[0].combined_day
    assert all(not day.combined_day for day in limited.days)


def test_fixed_coached_component_consumes_rowing_allocation_once():
    coached = AuthoritativeComponentFact("coach", START, 50, modality=TrainingModality.ON_WATER,
                                         purpose=TrainingComponentPurpose.COACHED)
    result = generate_training_days_v3(allocation(days=2, rowing=75), capacities(70, 70), (coached,))
    assert result.success
    assert category_minutes(result, PhysicalCategory.ROWING_SPECIFIC) == 75
    assert len([item for item in result.components if item.source_lineage_id == "authority:coach"]) == 1


def test_rest_unused_and_unavailable_are_distinct_empty_day_states():
    result = generate_training_days_v3(allocation(days=3), capacities(45, 45, 45, rest=(0,), unavailable=(2,)))
    assert result.success
    assert [day.state for day in result.days] == [TrainingDayState.DESIGNATED_REST, TrainingDayState.UNUSED_AVAILABLE, TrainingDayState.UNAVAILABLE]
    assert not result.components


def test_exact_r3_category_total_and_strength_exposure_conservation():
    result = generate_training_days_v3(allocation(days=7, rowing=90, strength_exposures=3, general=30), capacities(70, 70, 70, 70, 70, 70, 70))
    assert result.success
    assert category_minutes(result, PhysicalCategory.ROWING_SPECIFIC) == 90
    assert category_minutes(result, PhysicalCategory.STRENGTH) == 135
    assert category_minutes(result, PhysicalCategory.GENERAL_AEROBIC) == 30
    strength = [item for item in result.components if item.physical_category is PhysicalCategory.STRENGTH]
    assert len(strength) == 3 and {item.duration_minutes for item in strength} == {45}
    assert sum(day.total_physical_minutes for day in result.days) == 255


def test_component_identity_is_deterministic_move_stable_and_semantic_change_sensitive():
    common = dict(source_lineage_id="lineage:rowing:1", allocation_segment_id="r3:2026-01-05:2026-01-11",
                  physical_category=PhysicalCategory.ROWING_SPECIFIC, purpose=TrainingComponentPurpose.EASY_AEROBIC,
                  authority=ComponentAuthority.GENERATED)
    first = stable_component_identity(duration_minutes=30, **common)
    moved = stable_component_identity(duration_minutes=30, **common)
    replaced = stable_component_identity(duration_minutes=31, **common)
    assert first == moved
    assert first != replaced


def test_unknown_race_never_satisfies_r3_minutes_and_unclassified_authority_is_explicit():
    race = RaceEvent("race", START, None)
    unknown = AuthoritativeComponentFact("unknown", START, 30)
    result = generate_training_days_v3(allocation(rowing=30), capacities(30), (unknown,), race_context=(race,))
    assert result.success
    assert result.race_events == (race,)
    assert category_minutes(result, PhysicalCategory.ROWING_SPECIFIC) == 30
    assert any(item.code == "unresolved_authority_classification" for item in result.diagnostics)


def test_partial_terminal_segment_creates_only_actual_represented_dates():
    value = allocation(days=3, rowing=30)
    result = generate_training_days_v3(value, capacities(30, 0, 0))
    assert result.success
    assert [item.date for item in result.days] == [START, START + timedelta(days=1), START + timedelta(days=2)]


def test_other_can_only_split_when_r3_provenance_explicitly_allows_it():
    blocked = generate_training_days_v3(allocation(days=2, other=30, other_provenance=("explicit_other",)), capacities(15, 15))
    allowed = generate_training_days_v3(allocation(days=2, other=30, other_provenance=("explicit_other", "allow_split")), capacities(15, 15))
    assert not blocked.success
    assert any(item.code == "other_component_split_not_authorized" for item in blocked.infeasibilities)
    assert allowed.success
    assert [item.sequence for item in allowed.components] == [1, 1]


def test_component_calendar_fragment_is_additive_and_does_not_make_fake_legacy_session():
    result = generate_training_days_v3(allocation(strength_exposures=1, general=15), capacities(60))
    fragment = component_calendar_planversion_fragment(result)
    day = fragment["training_days"][0]
    assert fragment["component_calendar_version"] == "training-days-v1"
    assert "sessions" not in fragment
    assert day["total_physical_minutes"] == 60
    assert [item["physical_category"] for item in day["components"]] == ["strength", "general_aerobic"]
