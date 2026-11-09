"""M1 cross-training is typed history and profile capability, never prescription."""
from __future__ import annotations

import copy
import json
from datetime import date, timedelta

import pytest

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_adapters import adapt_optional_off_day_actuals
from rowing_plan.planning_domain import (
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Provenance,
    ProvenanceSource, TrainingModality, TrainingRole, normalize_training_modality,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.validators import validate_profile
from services.api.tests.disposable_browser_fixture import synthetic_profile


AS_OF = date(2026, 10, 12)
CONFIG = json.load(open("config/defaults.json"))


def _context(*facts: HistoricalFact) -> NormalizedPlanningContext:
    return NormalizedPlanningContext(
        planning_date=AS_OF, historical_facts=tuple(facts), authoritative_commitments=(),
        provisional_prior_placement_ids=(), race_context=(), authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _optional(activity_type: str, *, minutes: int = 45, intensity: str | None = "UT2") -> HistoricalFact:
    actual = {"status": "completed", "activity_type": activity_type, "actual_duration_min": minutes, "rpe": 3, "average_hr": 140}
    if intensity:
        actual["actual_intensity"] = intensity
    role = TrainingRole.GENERAL_AEROBIC if normalize_training_modality(activity_type) in {
        TrainingModality.BIKE, TrainingModality.ELLIPTICAL, TrainingModality.STAIR_CLIMBER, TrainingModality.RUNNING,
    } else TrainingRole.DEDICATED_UT2 if normalize_training_modality(activity_type) in {TrainingModality.ERG, TrainingModality.ON_WATER} else TrainingRole.STRENGTH if activity_type == "strength" else None
    return HistoricalFact(
        f"actual:{activity_type}", AS_OF - timedelta(days=1), HistoricalFactType.OPTIONAL_ACTIVITY,
        completed_actual=actual, physiological_credit=PhysiologicalCredit(role, 1, minutes, True) if role else PhysiologicalCredit(),
        provenance=Provenance(f"actual:{activity_type}", ProvenanceSource.OPTIONAL_ACTIVITY_LOG),
    )


@pytest.mark.parametrize(("alias", "expected"), [
    ("bike", TrainingModality.BIKE), ("bike_erg", TrainingModality.BIKE), ("stationary bike", TrainingModality.BIKE),
    ("cycling", TrainingModality.BIKE), ("run", TrainingModality.RUNNING), ("stairclimber", TrainingModality.STAIR_CLIMBER),
])
def test_modality_aliases_normalize_only_safe_known_values(alias, expected):
    assert normalize_training_modality(alias) is expected
    assert normalize_training_modality("mystery_machine") is None


@pytest.mark.parametrize("modality", ["bike", "elliptical", "stair_climber", "running"])
def test_easy_cross_training_is_general_aerobic_not_rowing_or_zone_credit(modality):
    state = reduce_physiological_state(_context(_optional(modality)))
    load = state.recent_7d_load
    assert (load.general_aerobic_minutes, load.general_aerobic_exposures) == (45, 1)
    assert load.rowing_minutes == load.low_intensity_rowing_minutes == 0
    assert state.completed_aerobic_exposure == PhysiologicalCredit()
    assert state.completed_aerobic_by_classification == {}


def test_unknown_cross_intensity_and_other_activity_remain_conservative():
    unknown = reduce_physiological_state(_context(_optional("bike", intensity=None)))
    other = reduce_physiological_state(_context(_optional("other", intensity="UT2")))
    assert unknown.recent_7d_load.general_aerobic_minutes == 0
    assert unknown.recent_7d_load.rowing_minutes == 0
    assert other.recent_7d_load.general_aerobic_minutes == other.recent_7d_load.rowing_minutes == 0


@pytest.mark.parametrize("modality", ["erg", "on_water"])
def test_easy_rowing_dual_dimension_does_not_double_physical_duration(modality):
    state = reduce_physiological_state(_context(_optional(modality, minutes=50)))
    load = state.recent_7d_load
    assert (load.rowing_minutes, load.general_aerobic_minutes) == (50, 50)
    assert state.completed_duration_minutes_by_fact[f"actual:{modality}"] == 50


def test_strength_is_not_general_aerobic_or_rowing_history():
    state = reduce_physiological_state(_context(_optional("strength", minutes=40)))
    load = state.recent_7d_load
    assert load.strength_exposures == 1
    assert (load.general_aerobic_minutes, load.general_aerobic_exposures, load.rowing_minutes) == (0, 0, 0)


def test_optional_history_adapter_canonicalizes_alias_without_store_migration():
    facts = adapt_optional_off_day_actuals({"actual:2026-10-11:bike_erg": {"status": "completed", "activity_type": "bike_erg", "actual_duration_min": 45, "actual_intensity": "UT2"}}, AS_OF)
    assert facts[0].physiological_credit.role is TrainingRole.GENERAL_AEROBIC
    assert facts[0].completed_actual["activity_type"] == "bike_erg"


def test_profile_cross_training_is_optional_validated_and_does_not_change_v3_output():
    base = synthetic_profile()
    enriched = copy.deepcopy(base)
    enriched["cross_training"] = {"modalities": {
        "bike": {"enabled": True, "familiarity": "regular", "preference": "prefer"},
        "elliptical": {"enabled": True, "familiarity": "some_experience", "preference": "neutral"},
        "running": {"enabled": True, "familiarity": "regular", "preference": "avoid"},
        "stair_climber": {"enabled": False, "familiarity": None, "preference": "neutral"},
    }}
    assert validate_profile(base) == validate_profile(enriched) == []
    for profile in (base, enriched):
        profile["season"] = {**profile["season"], "start_date": "2026-10-12", "end_date": "2026-10-26"}
    first = generate_plan_sequence_v3_candidate(base, CONFIG, build_intensity_profile(base, CONFIG), build_power_profile(base, CONFIG), planning_date=AS_OF)
    second = generate_plan_sequence_v3_candidate(enriched, CONFIG, build_intensity_profile(enriched, CONFIG), build_power_profile(enriched, CONFIG), planning_date=AS_OF)
    assert first.success and second.success
    assert first.plan["sessions"] == second.plan["sessions"]
    assert first.plan["calendar_days"] == second.plan["calendar_days"]


def test_profile_rejects_invalid_cross_training_values_but_allows_enabled_without_familiarity():
    profile = synthetic_profile()
    profile["cross_training"] = {"modalities": {"bike": {"enabled": True, "familiarity": None, "preference": "neutral"}}}
    assert validate_profile(profile) == []
    profile["cross_training"]["modalities"]["bike"]["preference"] = "always"
    assert "Cross-training bike preference is invalid." in validate_profile(profile)
