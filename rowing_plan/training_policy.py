"""Typed, non-operational policy contract for the replacement scheduler."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .planning_domain import RuleClass


@dataclass(frozen=True)
class TrainingPolicyRule:
    key: str
    value: Any
    rule_class: RuleClass
    source_ids: tuple[str, ...]
    rationale: str
    overrideability: str
    policy_version: str


PHYSIOLOGICAL_POLICY_VERSION = "physiological-state-1"

# Existing V2 recovery semantics, made explicit for the replacement reducer.
# These are interpretation rules only; the reducer never creates a session or
# decides a future training need.
DEFAULT_PHYSIOLOGICAL_POLICY = (
    TrainingPolicyRule(
        "completed_quality_minimum_spacing_days", 1,
        RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("scheduler_v2:quality_spacing",),
        "Mirror the existing continuous no-next-day quality spacing rule.",
        "coach_override", PHYSIOLOGICAL_POLICY_VERSION,
    ),
    TrainingPolicyRule(
        "completed_strength_minimum_spacing_days", 1,
        RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("scheduler_v2:strength_spacing",),
        "Mirror the existing continuous no-next-day strength spacing rule.",
        "coach_override", PHYSIOLOGICAL_POLICY_VERSION,
    ),
    TrainingPolicyRule(
        "completed_race_recovery_days_by_priority", {"A": 3, "B": 2, "C": 1},
        RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("periodization:RECOVERY",),
        "Use the existing priority-scaled post-race recovery span as a factual policy dependency.",
        "coach_override", PHYSIOLOGICAL_POLICY_VERSION,
    ),
)
