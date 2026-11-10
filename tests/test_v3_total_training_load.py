"""Top-level physical-duration contracts for V3 total training volume."""
from __future__ import annotations

from datetime import date, timedelta
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Placement,
    PlacementAuthorityStatus, Provenance, ProvenanceSource, TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.training_load_targets import (
    derive_total_training_load_targets, reconcile_total_training_load_targets,
)

AS_OF = date(2026, 11, 16)
CONFIG = json.load(open("config/defaults.json"))


def _profile(*, total: int | None = 360, rowing: int = 180) -> dict:
    athlete = {
        "experience_level": "intermediate", "training_environment": "erg",
        "current_approx_weekly_rowing_minutes": rowing,
    }
    if total is not None:
        athlete["current_weekly_training_minutes"] = total
    return {
        "athlete": athlete,
        "season": {"start_date": "2026-11-01", "end_date": "2026-12-31"},
        "training_context": {"current_strength_sessions_per_week": 2},
        "weekly_availability": [
            {"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
    }


def _context(*facts: HistoricalFact) -> NormalizedPlanningContext:
    return NormalizedPlanningContext(
        planning_date=AS_OF, historical_facts=tuple(facts), authoritative_commitments=(),
        provisional_prior_placement_ids=(), race_context=(), authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _fact(identifier: str, role: TrainingRole, minutes: int) -> HistoricalFact:
    activity = "strength" if role is TrainingRole.STRENGTH else "erg"
    return HistoricalFact(
        identifier, AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION,
        completed_actual={"status": "completed", "activity_type": activity, "actual_duration_min": minutes, "actual_intensity": "UT2"},
        physiological_credit=PhysiologicalCredit(role, 1, minutes, True),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG),
    )


def test_legacy_rowing_baseline_is_not_silently_reinterpreted_as_total_training():
    profile = _profile(total=None)
    context = _context()
    state = reduce_physiological_state(context, profile=profile)
    assert derive_total_training_load_targets(context, state, profile=profile) == ()


def test_total_training_ledger_counts_strength_and_endurance_once_without_double_counting_erg():
    profile = _profile()
    context = _context(_fact("erg", TrainingRole.DEDICATED_UT2, 60), _fact("strength", TrainingRole.STRENGTH, 45))
    state = reduce_physiological_state(context, profile=profile)
    assert state.recent_7d_load.physical_training_minutes == 105
    assert state.recent_7d_load.physical_endurance_minutes == 60
    target = derive_total_training_load_targets(context, state, profile=profile)[0]
    assert target.completed_physical_training_minutes == 105


def test_strength_reservation_consumes_the_shared_total_budget_once():
    profile = _profile(total=120)
    context = _context()
    state = reduce_physiological_state(context, profile=profile)
    target = derive_total_training_load_targets(context, state, profile=profile)[0]
    strength = Placement(
        "strength", "strength", AS_OF, 45, PhysiologicalCredit(TrainingRole.STRENGTH, 1, 0, True),
        Provenance("strength", ProvenanceSource.LEGACY_V2), PlacementAuthorityStatus.PROVISIONAL,
        role=TrainingRole.STRENGTH,
    )
    erg = Placement(
        "erg", "erg", AS_OF + timedelta(days=1), 60, PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 60, True),
        Provenance("erg", ProvenanceSource.LEGACY_V2), PlacementAuthorityStatus.PROVISIONAL,
        role=TrainingRole.DEDICATED_UT2,
    )
    result = reconcile_total_training_load_targets((target,), (strength, erg))[0]
    assert (result.selected_physical_training_minutes, result.selected_strength_minutes, result.selected_endurance_minutes, result.selected_rowing_specific_minutes) == (105, 45, 60, 60)
    assert result.selected_physical_training_minutes <= result.maximum_minutes


def test_erg_only_winter_profile_with_explicit_total_budget_keeps_strength_and_multiple_erg_sessions():
    profile = _profile(total=360)
    candidate = generate_plan_sequence_v3_candidate(
        profile, CONFIG, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG),
        planning_date=AS_OF,
    )
    assert candidate.success
    sessions = [item for item in candidate.plan["sessions"] if AS_OF <= date.fromisoformat(item["date"]) < AS_OF + timedelta(days=7)]
    erg = [item for item in sessions if item["mode"] == "erg"]
    strength = [item for item in sessions if item["mode"] == "strength"]
    assert len(erg) >= 2
    assert strength
    assert not any(item["mode"] == "on_water" for item in sessions)
    total = next(item for item in candidate.plan["v2_diagnostics"]["total_training_load_targets"] if item["horizon_days"] == 7)
    assert total["selected_strength_minutes"] > 0
    assert total["selected_total_physical_minutes"] <= total["total_training_maximum"]
    assert total["unmet_total_training_minutes"] >= 0
