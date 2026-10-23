from __future__ import annotations

from dataclasses import replace

from rowing_plan.planning_domain import RuleClass
from rowing_plan.training_policy import (
    DEFAULT_TRAINING_POLICY,
    POLICY_VERSION,
    TrainingPolicy,
    TrainingPolicyRule,
    repository_source_ids,
    validate_training_policy,
)


def _rule(
    key: str,
    value: object,
    rule_class: RuleClass,
    source_ids: tuple[str, ...] = (),
    rationale: str = "A clearly classified operational rule.",
) -> TrainingPolicyRule:
    return TrainingPolicyRule(
        key=key,
        value=value,
        rule_class=rule_class,
        source_ids=source_ids,
        rationale=rationale,
        overrideability="coach_override",
        policy_version=POLICY_VERSION,
    )


def test_default_policy_is_typed_complete_and_valid() -> None:
    policy = DEFAULT_TRAINING_POLICY

    assert policy.version == POLICY_VERSION
    assert all((
        policy.phase_policy,
        policy.aerobic_policy,
        policy.quality_policy,
        policy.strength_policy,
        policy.recovery_policy,
        policy.taper_policy,
        policy.race_policy,
        policy.spacing_policy,
        policy.progression_policy,
    ))
    assert validate_training_policy(policy) == ()


def test_policy_has_no_weekday_physiology_or_mandatory_weekly_rest() -> None:
    serialized = DEFAULT_TRAINING_POLICY.to_dict()
    text = repr(serialized).lower()

    assert not any(day in text for day in (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    ))
    assert "mandatory_weekly_rest" not in {item.key for item in DEFAULT_TRAINING_POLICY.rules}
    assert "weekly_rest" not in text


def test_taper_separates_research_principle_from_configurable_window() -> None:
    principle = DEFAULT_TRAINING_POLICY.rule("taper_reduce_volume_retain_useful_intensity_frequency")
    window = DEFAULT_TRAINING_POLICY.rule("taper_days_by_priority")

    assert principle.rule_class is RuleClass.RESEARCH_CONSTRAINED
    assert principle.source_ids == ("S009",)
    assert window.rule_class is RuleClass.EVIDENCE_INFORMED_HEURISTIC
    assert window.value == {"A": 10, "B": 5, "C": 2}


def test_strength_target_and_race_recovery_spans_are_honest_heuristics() -> None:
    strength = DEFAULT_TRAINING_POLICY.rule("rolling_strength_target_14d")
    recovery = DEFAULT_TRAINING_POLICY.rule("race_recovery_days_by_priority")

    assert strength.rule_class is RuleClass.EVIDENCE_INFORMED_HEURISTIC
    assert strength.value == {"target": 4, "minimum": 3, "window_days": 14}
    assert recovery.rule_class is RuleClass.EVIDENCE_INFORMED_HEURISTIC
    assert recovery.value == {"A": 3, "B": 2, "C": 1}


def test_low_intensity_and_progression_do_not_claim_universal_ratios_or_protocols() -> None:
    low_intensity = DEFAULT_TRAINING_POLICY.rule("low_intensity_emphasis")
    two_k = DEFAULT_TRAINING_POLICY.rule("two_k_is_integrated_performance_anchor")
    short_power = DEFAULT_TRAINING_POLICY.rule("short_power_tests_are_supplemental")

    assert low_intensity.rule_class is RuleClass.EVIDENCE_INFORMED_HEURISTIC
    assert low_intensity.value == "predominant_not_fixed_percentage"
    assert two_k.rule_class is RuleClass.RESEARCH_CONSTRAINED
    assert short_power.rule_class is RuleClass.RESEARCH_CONSTRAINED
    assert two_k.key != short_power.key


def test_no_automatic_demographic_modifier_is_part_of_the_policy_contract() -> None:
    rule = DEFAULT_TRAINING_POLICY.rule("no_automatic_demographic_multiplier")

    assert rule.rule_class is RuleClass.RESEARCH_CONSTRAINED
    assert rule.source_ids == ("S026",)
    assert rule.value is True


def test_software_choices_are_explicit_and_cannot_masquerade_as_research() -> None:
    implementation_keys = {item.key for item in DEFAULT_TRAINING_POLICY.implementation_policy}
    assert {"deterministic_lexical_tie_break", "beam_search_width", "serialization_is_deterministic"} <= implementation_keys

    invalid = TrainingPolicy(
        version=POLICY_VERSION,
        implementation_policy=(
            _rule("implementation_claim", 3, RuleClass.SOFTWARE_IMPLEMENTATION_CHOICE, ("S009",), "Research establishes this search implementation."),
        ),
    )
    assert validate_training_policy(invalid) == (
        "software_rule_cannot_claim_research_rationale:implementation_claim",
        "software_rule_cannot_claim_research_source:implementation_claim",
    )


def test_policy_serialization_is_stable_when_definition_order_changes() -> None:
    reordered = replace(
        DEFAULT_TRAINING_POLICY,
        aerobic_policy=tuple(reversed(DEFAULT_TRAINING_POLICY.aerobic_policy)),
        recovery_policy=tuple(reversed(DEFAULT_TRAINING_POLICY.recovery_policy)),
    )

    assert reordered.to_dict() == DEFAULT_TRAINING_POLICY.to_dict()


def test_every_research_source_is_registered() -> None:
    registered = repository_source_ids()
    research_rules = [
        item for item in DEFAULT_TRAINING_POLICY.rules
        if item.rule_class is RuleClass.RESEARCH_CONSTRAINED
    ]

    assert research_rules
    assert all(item.source_ids for item in research_rules)
    assert all(source_id in registered for item in research_rules for source_id in item.source_ids)


def test_validator_rejects_weekday_numeric_and_conflicting_key_contract_violations() -> None:
    weekday = _rule("monday_quality", 1, RuleClass.EVIDENCE_INFORMED_HEURISTIC)
    missing_research_source = _rule("research_without_source", True, RuleClass.RESEARCH_CONSTRAINED)
    no_rationale = _rule("numeric_without_rationale", 2, RuleClass.EVIDENCE_INFORMED_HEURISTIC, rationale="")
    duplicate_one = _rule("same_key", "one", RuleClass.COACH_OR_ATHLETE_PREFERENCE)
    duplicate_two = _rule("same_key", "two", RuleClass.COACH_OR_ATHLETE_PREFERENCE)
    invalid = TrainingPolicy(
        version=POLICY_VERSION,
        phase_policy=(weekday, missing_research_source, no_rationale, duplicate_one),
        preference_policy=(duplicate_two,),
    )

    assert validate_training_policy(invalid) == (
        "duplicate_rule_key:same_key",
        "numeric_rule_requires_rationale_and_class:numeric_without_rationale",
        "research_rule_requires_source:research_without_source",
        "weekday_physiology_not_allowed:monday_quality",
    )
