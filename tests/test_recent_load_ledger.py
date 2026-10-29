from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.continuous_placement import PlacementSearchConfig, place_continuous_training_needs
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Provenance,
    ProvenanceSource, TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.training_needs import generate_training_need_plan
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
import json


AS_OF = date(2026, 3, 15)  # Sunday; windows are deliberately weekday-neutral.


def _fact(identifier: str, day: date, *, role: TrainingRole | None = None, minutes: int = 0,
          kind: HistoricalFactType = HistoricalFactType.COMPLETED_PLANNED_SESSION,
          intensity: str | None = None) -> HistoricalFact:
    actual = {"status": "completed", "actual_duration_min": minutes}
    if intensity:
        actual["actual_intensity"] = intensity
    return HistoricalFact(
        identifier, day, kind, completed_actual=actual if kind is not HistoricalFactType.RACE_FACT else None,
        planned_prescription={"priority": "B"} if kind is HistoricalFactType.RACE_FACT else None,
        physiological_credit=PhysiologicalCredit(role, 1, minutes, True) if role else PhysiologicalCredit(),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _context(*facts: HistoricalFact, as_of: date = AS_OF, duplicates: int = 0) -> NormalizedPlanningContext:
    return NormalizedPlanningContext(
        planning_date=as_of, historical_facts=tuple(facts), authoritative_commitments=(),
        provisional_prior_placement_ids=(), race_context=(), authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts), duplicates_collapsed=duplicates),
    )


def _profile() -> dict:
    return {
        "athlete": {"current_approx_weekly_rowing_minutes": 180},
        "season": {"start_date": "2026-03-01", "end_date": "2026-05-01"},
    }


def test_recent_load_uses_trailing_half_open_windows_and_ages_out_old_work():
    exact_7 = _fact("exact-7", AS_OF - timedelta(days=7), role=TrainingRole.DEDICATED_UT2, minutes=40, intensity="UT2")
    before_7 = _fact("before-7", AS_OF - timedelta(days=8), role=TrainingRole.DEDICATED_UT2, minutes=50, intensity="UT2")
    exact_14 = _fact("exact-14", AS_OF - timedelta(days=14), role=TrainingRole.STRENGTH, minutes=40)
    before_14 = _fact("before-14", AS_OF - timedelta(days=15), role=TrainingRole.STRENGTH, minutes=40)
    state = reduce_physiological_state(_context(exact_7, before_7, exact_14, before_14), profile=_profile())

    assert (state.recent_7d_load.window_start, state.recent_7d_load.as_of_date) == (date(2026, 3, 8), AS_OF)
    assert state.recent_7d_load.rowing_minutes == 40
    assert state.recent_14d_load.rowing_minutes == 90
    assert state.recent_14d_load.strength_exposures == 1
    assert "before-14" in state.recent_14d_load.excluded_outside_window_fact_ids
    assert state.completed_strength_exposure.exposures == 1


def test_old_completed_work_does_not_suppress_current_need_but_recent_work_does():
    old = _fact("old", AS_OF - timedelta(days=15), role=TrainingRole.DEDICATED_UT2, minutes=90, intensity="UT2")
    recent = _fact("recent", AS_OF - timedelta(days=6), role=TrainingRole.DEDICATED_UT2, minutes=90, intensity="UT2")
    old_needs = generate_training_need_plan(_context(old), profile=_profile()).needs
    recent_needs = generate_training_need_plan(_context(recent), profile=_profile()).needs

    aerobic_roles = {TrainingRole.LONG_AEROBIC, TrainingRole.DEDICATED_UT2}
    assert any(item.role in aerobic_roles for item in old_needs)
    assert not any(item.role in aerobic_roles for item in recent_needs)


def test_optional_actual_and_race_count_once_then_age_out_without_weekday_identity():
    optional = _fact("optional-erg", AS_OF - timedelta(days=2), role=TrainingRole.DEDICATED_UT2, minutes=30,
                     kind=HistoricalFactType.OPTIONAL_ACTIVITY, intensity="UT2")
    race = _fact("race", AS_OF - timedelta(days=2), role=None, minutes=20, kind=HistoricalFactType.RACE_FACT)
    state = reduce_physiological_state(_context(optional, race, duplicates=1), profile=_profile())
    later = reduce_physiological_state(_context(optional, race), profile=_profile())
    aged = reduce_physiological_state(_context(optional, race, as_of=AS_OF + timedelta(days=13)), profile=_profile())

    assert (state.recent_7d_load.optional_activity_minutes, state.recent_7d_load.race_exposures) == (30, 1)
    assert state.recent_7d_load.duplicate_facts_excluded == 1
    assert (later.recent_7d_load.optional_activity_minutes, later.recent_7d_load.race_exposures) == (30, 1)
    assert (aged.recent_14d_load.optional_activity_minutes, aged.recent_14d_load.race_exposures) == (0, 0)


def test_multi_exposure_strength_needs_are_atomic_and_place_subject_to_spacing():
    context = _context()
    needs = [item for item in generate_training_need_plan(context, profile=_profile()).needs if item.role is TrainingRole.STRENGTH]
    assert len(needs) == 3
    assert all(item.target_credit.exposures == 1 and item.exposure_count == 3 for item in needs)

    unconstrained = place_continuous_training_needs(
        context, reduce_physiological_state(context, profile=_profile()), needs, profile=_profile(),
        config=PlacementSearchConfig(horizon_days=14, beam_width=32),
    )
    unconstrained_dates = [item.date for item in unconstrained.placements if item.role is TrainingRole.STRENGTH]
    assert unconstrained_dates == [AS_OF, AS_OF + timedelta(days=2), AS_OF + timedelta(days=4)]

    profile = {
        **_profile(),
        "date_availability": [
            {"date": (AS_OF + timedelta(days=index)).isoformat(), "available": index in {0, 2}, "max_training_minutes": 90}
            for index in range(14)
        ],
    }
    result = place_continuous_training_needs(
        context, reduce_physiological_state(context, profile=profile), needs, profile=profile,
        config=PlacementSearchConfig(horizon_days=14, beam_width=32),
    )
    placed = [item for item in result.placements if item.role is TrainingRole.STRENGTH]

    assert len(placed) == 2
    assert result.diagnostics.repeated_needs_placed_by_role == {"strength": 2}
    assert result.diagnostics.repeated_needs_unplaced_by_role == {"strength": 1}
    assert len(result.diagnostics.unplaced_needs) == 1


def test_equivalent_chronology_across_monday_boundary_has_same_recent_load():
    monday = date(2026, 3, 16)
    sunday = monday - timedelta(days=1)
    first = reduce_physiological_state(_context(_fact("a", sunday - timedelta(days=2), role=TrainingRole.DEDICATED_UT2, minutes=45, intensity="UT2"), as_of=sunday), profile=_profile())
    second = reduce_physiological_state(_context(_fact("b", monday - timedelta(days=2), role=TrainingRole.DEDICATED_UT2, minutes=45, intensity="UT2"), as_of=monday), profile=_profile())
    assert (first.recent_7d_load.rowing_minutes, first.recent_7d_load.low_intensity_rowing_minutes) == (second.recent_7d_load.rowing_minutes, second.recent_7d_load.low_intensity_rowing_minutes)


def test_v3_candidate_exposes_non_sensitive_rolling_load_and_repeat_diagnostics():
    profile = _profile()
    config = json.load(open("config/defaults.json"))
    candidate = generate_plan_sequence_v3_candidate(
        profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config),
        planning_date=AS_OF,
    )
    assert candidate.success
    diagnostics = candidate.plan["v2_diagnostics"]
    assert set(diagnostics["recent_completed_load"]) == {"7d", "14d"}
    assert diagnostics["repeated_exposure_needs"]["generated_by_role"] == {"strength": 3}
