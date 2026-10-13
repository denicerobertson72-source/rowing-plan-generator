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
