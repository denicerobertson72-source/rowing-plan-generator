"""Versioned, auditable policy vocabulary for the replacement planner.

The policy is descriptive in this milestone. It deliberately does not select
needs, dates, or workouts, and live Scheduler V2 does not consume it.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .planning_domain import RuleClass


POLICY_VERSION = "training-policy-3.0"
PHYSIOLOGICAL_POLICY_VERSION = POLICY_VERSION
_WEEKDAY_WORDS = frozenset({"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})


@dataclass(frozen=True)
class TrainingPolicyRule:
    key: str
    value: Any
    rule_class: RuleClass
    source_ids: tuple[str, ...]
    rationale: str
    overrideability: str
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "rule_class": self.rule_class.value, "source_ids": list(self.source_ids), "rationale": self.rationale, "overrideability": self.overrideability, "policy_version": self.policy_version}


@dataclass(frozen=True)
class TrainingPolicy:
    """Replacement-planner rules grouped by concern, never by weekday."""

    version: str
    phase_policy: tuple[TrainingPolicyRule, ...] = ()
    aerobic_policy: tuple[TrainingPolicyRule, ...] = ()
    quality_policy: tuple[TrainingPolicyRule, ...] = ()
    strength_policy: tuple[TrainingPolicyRule, ...] = ()
    recovery_policy: tuple[TrainingPolicyRule, ...] = ()
    taper_policy: tuple[TrainingPolicyRule, ...] = ()
    race_policy: tuple[TrainingPolicyRule, ...] = ()
    spacing_policy: tuple[TrainingPolicyRule, ...] = ()
    progression_policy: tuple[TrainingPolicyRule, ...] = ()
    preference_policy: tuple[TrainingPolicyRule, ...] = ()
    implementation_policy: tuple[TrainingPolicyRule, ...] = ()

    @property
    def rules(self) -> tuple[TrainingPolicyRule, ...]:
        return tuple(sorted((*self.phase_policy, *self.aerobic_policy, *self.quality_policy, *self.strength_policy, *self.recovery_policy, *self.taper_policy, *self.race_policy, *self.spacing_policy, *self.progression_policy, *self.preference_policy, *self.implementation_policy), key=lambda item: item.key))

    def rule(self, key: str) -> TrainingPolicyRule:
        return next(item for item in self.rules if item.key == key)

    def to_dict(self) -> dict[str, Any]:
        def section(items: Iterable[TrainingPolicyRule]) -> list[dict[str, Any]]:
            return [item.to_dict() for item in sorted(items, key=lambda item: item.key)]
        return {"version": self.version, "phase_policy": section(self.phase_policy), "aerobic_policy": section(self.aerobic_policy), "quality_policy": section(self.quality_policy), "strength_policy": section(self.strength_policy), "recovery_policy": section(self.recovery_policy), "taper_policy": section(self.taper_policy), "race_policy": section(self.race_policy), "spacing_policy": section(self.spacing_policy), "progression_policy": section(self.progression_policy), "preference_policy": section(self.preference_policy), "implementation_policy": section(self.implementation_policy)}


def repository_source_ids() -> frozenset[str]:
    """Read the existing source register; citations are never fabricated."""
    source_file = Path(__file__).resolve().parents[1] / "sources" / "source_register.csv"
    with source_file.open(newline="", encoding="utf-8") as handle:
        return frozenset(row["source_id"] for row in csv.DictReader(handle) if row.get("source_id"))


def _contains_weekday(value: Any) -> bool:
    if isinstance(value, str):
        return any(word in value.lower() for word in _WEEKDAY_WORDS)
    if isinstance(value, dict):
        return any(_contains_weekday(key) or _contains_weekday(item) for key, item in value.items())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_weekday(item) for item in value)
    return False


def _is_numeric_operational(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    if isinstance(value, dict):
        return any(_is_numeric_operational(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_is_numeric_operational(item) for item in value)
    return False


def validate_training_policy(policy: TrainingPolicy, *, source_ids: frozenset[str] | None = None) -> tuple[str, ...]:
    """Return deterministic policy-contract violations without mutation."""
    known_sources = source_ids if source_ids is not None else repository_source_ids()
    errors: list[str] = []
    seen: set[str] = set()
    for rule in policy.rules:
        if rule.key in seen:
            errors.append(f"duplicate_rule_key:{rule.key}")
        seen.add(rule.key)
        if rule.policy_version != policy.version:
            errors.append(f"rule_version_mismatch:{rule.key}")
        if rule.rule_class is RuleClass.RESEARCH_CONSTRAINED:
            if not rule.source_ids:
                errors.append(f"research_rule_requires_source:{rule.key}")
            for source_id in rule.source_ids:
                if source_id not in known_sources:
                    errors.append(f"unknown_research_source:{rule.key}:{source_id}")
        if rule.rule_class is RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE:
            if any(source_id.startswith("S") for source_id in rule.source_ids):
                errors.append(f"software_rule_cannot_claim_research_source:{rule.key}")
            if "research" in rule.rationale.lower():
                errors.append(f"software_rule_cannot_claim_research_rationale:{rule.key}")
        if rule.rule_class is not RuleClass.COACH_OR_ATHLETE_PREFERENCE and _contains_weekday((rule.key, rule.value, rule.rationale)):
            errors.append(f"weekday_physiology_not_allowed:{rule.key}")
        if _is_numeric_operational(rule.value) and (not rule.rationale or not rule.rule_class):
            errors.append(f"numeric_rule_requires_rationale_and_class:{rule.key}")
    return tuple(sorted(errors))


def _rule(key: str, value: Any, rule_class: RuleClass, source_ids: tuple[str, ...], rationale: str, overrideability: str) -> TrainingPolicyRule:
    return TrainingPolicyRule(key, value, rule_class, source_ids, rationale, overrideability, POLICY_VERSION)


DEFAULT_TRAINING_POLICY = TrainingPolicy(
    version=POLICY_VERSION,
    phase_policy=(
        _rule("generic_bands_are_configurable_not_measured_thresholds", True, RuleClass.RESEARCH_CONSTRAINED, ("S020",), "Generic bands are coach/configuration guidance unless measured thresholds support them.", "coach_override"),
        _rule("physiology_is_weekday_neutral", True, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Chronological physiology must not depend on display-week identity.", "not_applicable"),
    ),
    aerobic_policy=(
        _rule("completed_work_drives_completed_state", True, RuleClass.RESEARCH_CONSTRAINED, ("S018",), "Completed work, not a prescription, is the available physiological-history evidence.", "not_applicable"),
        _rule("uncompleted_prescription_has_no_completed_credit", True, RuleClass.RESEARCH_CONSTRAINED, ("S018",), "A planned but uncompleted prescription remains audit history and does not become completed physiological load.", "not_applicable"),
        _rule("low_intensity_emphasis", "predominant_not_fixed_percentage", RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S010", "S011"), "Rowing intensity distribution varies; maintain ordinary low-intensity emphasis without a universal ratio.", "coach_override"),
        _rule("mode_specific_accounting", True, RuleClass.RESEARCH_CONSTRAINED, ("S018",), "Cardiovascular and rowing-specific work remain distinct accounting concepts.", "not_applicable"),
        _rule("dedicated_ut2_target_credit_minutes", 90, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Replacement-planner starting dose for a rolling aerobic need; athlete capacity may reduce it.", "coach_override"),
        _rule("long_aerobic_target_credit_minutes", 75, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Replacement-planner starting dose for a long-aerobic exposure; athlete capacity may reduce it.", "coach_override"),
        _rule("ordinary_need_horizon_days", 14, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "A continuous planning horizon, not a display-week obligation.", "coach_override"),
        _rule("load_target_range_fractions", {"minimum": 0.80, "maximum": 1.10}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S010",), "Individual baseline volume is represented as a range, not a universal weekly prescription.", "coach_override"),
        _rule("low_intensity_minimum_fraction", 0.70, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S010", "S011"), "Ordinary rowing volume should remain predominantly low intensity without asserting a universal ratio.", "coach_override"),
        _rule("load_progression_guard", {"maximum_increase": 0.15, "baseline_floor": 0.65}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S010",), "Recent completed load tempers abrupt prescription changes while a single low week does not erase established baseline.", "coach_override"),
        _rule("residual_aerobic_session_minutes", {"minimum": 30, "preferred": 60}, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Split a material residual into practical continuous exposures without a session-count stencil.", "coach_override"),
        _rule("default_weekly_rowing_minutes_by_experience", {"new": 90, "novice": 90, "developing": 120, "intermediate": 150, "experienced": 180, "competitive": 210}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Fallback only when no athlete baseline is supplied; values are coaching defaults, not research-derived universals.", "coach_override"),
    ),
    quality_policy=(
        _rule("quality_categories_are_bounded_not_protocols", ("AT", "TR", "AN", "PP"), RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S014", "S015", "S016", "S020"), "Evidence supports categories, not copied universal protocols or exposure counts.", "coach_override"),
        _rule("quality_dose_requires_phase_and_athlete_context", True, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S010", "S020"), "Exposure and minute ranges require phase, completed work, and athlete response; no universal number is asserted.", "coach_override"),
        _rule("ordinary_race_specific_work_is_tr_unless_explicitly_anaerobic_or_power", True, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Preserve the current generic-band interpretation and avoid inventing AN or PP.", "coach_override"),
        _rule("quality_target_credit_minutes", 35, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "A single bounded quality exposure is a starting need magnitude, subject to phase and athlete response.", "coach_override"),
    ),
    strength_policy=(
        _rule("concurrent_strength_endurance_is_individualized", True, RuleClass.RESEARCH_CONSTRAINED, ("S017",), "Concurrent training can be useful, while interference and recovery remain individual.", "coach_override"),
        _rule("rolling_strength_target_14d", {"target": 4, "minimum": 3, "window_days": 14}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S017",), "Current app coaching target; evidence does not establish this exact count.", "coach_override"),
        _rule("taper_strength_continuity", {"target": 1, "minimum": 0}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S017",), "Taper may retain a reduced strength-maintenance exposure; exact frequency remains a coaching heuristic.", "coach_override"),
    ),
    recovery_policy=(
        _rule("race_hard_recovery_exclusions", ("quality", "strength"), RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S009", "S019"), "Post-race recovery protects hard work while leaving low-load eligibility context-specific.", "coach_override"),
        _rule("race_recovery_days_by_priority", {"A": 3, "B": 2, "C": 1}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Existing app priority-scaled recovery durations are useful heuristics, not biological laws.", "coach_override"),
        _rule("race_soft_recovery_days_by_priority", {"A": 5, "B": 3, "C": 2}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "A short lower-load transition after hard race recovery is an app heuristic, not a biological law.", "coach_override"),
        _rule("soft_recovery_credit_multiplier", 0.5, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Suggested reduced credit during a soft-recovery transition; later coaching may override it.", "coach_override"),
        _rule("multiple_race_load_additional_recovery_day", 1, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Existing app adjustment for a multi-race event; retain as an explicit heuristic.", "coach_override"),
    ),
    taper_policy=(
        _rule("taper_reduce_volume_retain_useful_intensity_frequency", True, RuleClass.RESEARCH_CONSTRAINED, ("S009",), "Taper policy reduces volume while retaining useful intensity/frequency as appropriate.", "coach_override"),
        _rule("taper_days_by_priority", {"A": 10, "B": 5, "C": 2}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Existing app taper windows are configurable scheduling heuristics, not universal prescriptions.", "coach_override"),
        _rule("taper_volume_multiplier", 0.6, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Replacement-planner starting volume reduction; the research principle does not establish one universal percentage.", "coach_override"),
    ),
    race_policy=(
        _rule("race_is_completed_stress_once", True, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "A normalized completed race produces one stress record even when legacy sources repeat it.", "not_applicable"),
        _rule("course_practice_is_not_race_stress", True, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Technical familiarization remains distinct from competition stress.", "coach_override"),
    ),
    spacing_policy=(
        _rule("quality_minimum_spacing_days", 1, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("scheduler_v2:quality_spacing",), "Mirror the current continuous no-next-day quality guard; this is an implementation guard, not a physiological duration.", "coach_override"),
        _rule("strength_minimum_spacing_days", 1, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("scheduler_v2:strength_spacing",), "Mirror the current continuous no-next-day strength guard; this is an implementation guard, not a physiological duration.", "coach_override"),
    ),
    progression_policy=(
        _rule("two_k_is_integrated_performance_anchor", True, RuleClass.RESEARCH_CONSTRAINED, ("S012", "S013", "S026", "S029"), "Actual 2K remains the principal integrated rowing-performance anchor.", "athlete_test_update"),
        _rule("short_power_tests_are_supplemental", ("60_second", "short_peak"), RuleClass.RESEARCH_CONSTRAINED, ("S021", "S022", "S023", "S026", "S027", "S028"), "Short power tests refine within-athlete profiling and do not replace an actual 2K.", "athlete_test_update"),
        _rule("progression_overload_unload_are_contextual", True, RuleClass.EVIDENCE_INFORMED_HEURISTIC, ("S010",), "Progression and unloading depend on completed work, race context, and athlete response rather than a fixed stencil.", "coach_override"),
    ),
    preference_policy=(
        _rule("profile_availability_and_fixed_commitments_are_preferences_or_authority", True, RuleClass.COACH_OR_ATHLETE_PREFERENCE, (), "Profile days, coaching commitments, user fixes, and bands are interpreted outside physiological policy.", "athlete_or_coach"),
        _rule("no_automatic_demographic_multiplier", True, RuleClass.RESEARCH_CONSTRAINED, ("S026",), "No sex, menstrual-cycle, contraception, menopause, or age multiplier is applied without athlete-specific evidence.", "not_applicable"),
    ),
    implementation_policy=(
        _rule("deterministic_lexical_tie_break", True, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Determinism is an execution property, not physiology.", "not_applicable"),
        _rule("beam_search_width", "implementation_config", RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Search width belongs to solver configuration, not training policy.", "not_applicable"),
        _rule("serialization_is_deterministic", True, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, (), "Stable serialization supports auditability without making physiological claims.", "not_applicable"),
    ),
)

# Backwards-compatible reducer seam. This subset is not routed into live V2.
DEFAULT_PHYSIOLOGICAL_POLICY = (
    _rule("completed_quality_minimum_spacing_days", 1, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("scheduler_v2:quality_spacing",), "Mirror the current continuous no-next-day quality spacing rule.", "coach_override"),
    _rule("completed_strength_minimum_spacing_days", 1, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("scheduler_v2:strength_spacing",), "Mirror the current continuous no-next-day strength spacing rule.", "coach_override"),
    _rule("completed_race_recovery_days_by_priority", {"A": 3, "B": 2, "C": 1}, RuleClass.EVIDENCE_INFORMED_HEURISTIC, (), "Existing app race recovery spans are configurable heuristics, not biological laws.", "coach_override"),
)
