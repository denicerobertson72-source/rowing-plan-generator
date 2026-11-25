"""Evidence-informed, diagnostic-only developmental recommendations for V3.

This module intentionally does not create dose, quality, workout, calendar, or
assessment-scheduling decisions.  It provides the future development-planning
authority while the legacy limiter continues to own the existing R6 path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping

from .physiological_evidence import PhysiologicalEvidenceProfile


RECOMMENDATION_VERSION = "r9f3c-development-1"

# Approved application guidance.  These are deliberately neither retest
# deadlines nor active/persistent blocks; R9F.6 owns that later state.
PROVISIONAL_DEVELOPMENTAL_BLOCK_GUIDANCE: Mapping[str, tuple[int | None, int | None, str]] = {
    "peak_power_development": (4, 6, "provisional_configurable_guidance"),
    "maximum_strength_development": (6, 12, "provisional_configurable_guidance"),
    "anaerobic_performance_development": (4, 6, "provisional_configurable_guidance"),
    "aerobic_strength_endurance": (6, 8, "provisional_configurable_guidance"),
    "aerobic_capacity_development": (6, 8, "provisional_configurable_guidance"),
    "threshold_development_context": (4, 8, "provisional_configurable_guidance"),
    "sprint_specific_preparation": (4, 6, "provisional_configurable_guidance"),
    "head_race_specific_preparation": (4, 8, "provisional_configurable_guidance"),
    "race_specific_preparation_context": (None, None, "season_context_not_a_block_deadline"),
    "technical_efficiency": (None, None, "ongoing_not_a_block_deadline"),
    "recovery_reconditioning": (None, None, "individualized_not_a_block_deadline"),
    "foundational_general_preparation": (None, None, "context_dependent_not_a_block_deadline"),
}


@dataclass(frozen=True)
class AssessmentCandidate:
    protocol: str
    modality: str
    candidate_type: str
    developmental_question: str
    rationale: tuple[str, ...]
    seasonal_context: str
    defer_conditions: tuple[str, ...]
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rationale", tuple(sorted(set(self.rationale))))
        object.__setattr__(self, "defer_conditions", tuple(sorted(set(self.defer_conditions))))
        object.__setattr__(self, "evidence_references", tuple(sorted(set(self.evidence_references))))


@dataclass(frozen=True)
class DevelopmentalBlockGuidance:
    emphasis: str
    minimum_weeks: int | None
    maximum_weeks: int | None
    status: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations))))


@dataclass(frozen=True)
class DevelopmentRecommendation:
    """An auditable recommendation, explicitly separate from diagnosis/prescription."""

    recommendation_version: str
    diagnostic_only: bool
    recommendation_type: str
    decision_basis: str
    primary_emphasis: str
    secondary_emphasis: str | None
    complementary_capabilities_to_maintain: tuple[str, ...]
    rationale: tuple[str, ...]
    athlete_goal_context: tuple[str, ...]
    season_context: tuple[str, ...]
    training_history_context: tuple[str, ...]
    recovery_context: tuple[str, ...]
    evidence_references: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    alternative_considerations: tuple[str, ...]
    confidence: str
    limitations: tuple[str, ...]
    next_most_informative_assessments: tuple[AssessmentCandidate, ...]
    revisit_conditions: tuple[str, ...]
    block_guidance: DevelopmentalBlockGuidance
    physiological_diagnosis: str = "not_made"
    r6_prescription_status: str = "unchanged_legacy_path"
    provenance: tuple[str, ...] = (RECOMMENDATION_VERSION,)

    def __post_init__(self) -> None:
        for field in (
            "complementary_capabilities_to_maintain", "rationale", "athlete_goal_context",
            "season_context", "training_history_context", "recovery_context", "evidence_references",
            "evidence_gaps", "alternative_considerations", "limitations", "revisit_conditions", "provenance",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        object.__setattr__(
            self, "next_most_informative_assessments",
            tuple(sorted(self.next_most_informative_assessments, key=lambda item: (item.protocol, item.modality))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _goals(profile: Mapping[str, Any]) -> tuple[str | None, str | None, tuple[str, ...]]:
    current = _mapping(profile.get("planning_goals"))
    primary = current.get("primary_goal") if isinstance(current.get("primary_goal"), str) else None
    secondary = current.get("secondary_goal") if isinstance(current.get("secondary_goal"), str) else None
    if primary:
        return primary, secondary, (f"planning_goal:{primary}", *( (f"secondary_goal:{secondary}",) if secondary else ()))
    legacy = profile.get("goals", ())
    if isinstance(legacy, (list, tuple)):
        ordered = sorted(
            (item for item in legacy if isinstance(item, Mapping) and isinstance(item.get("goal_type"), str)),
            key=lambda item: (item.get("priority") != "primary", str(item.get("goal_type"))),
        )
        if ordered:
            primary = str(ordered[0]["goal_type"])
            secondary = str(ordered[1]["goal_type"]) if len(ordered) > 1 else None
            return primary, secondary, (f"legacy_goal:{primary}", *( (f"secondary_legacy_goal:{secondary}",) if secondary else ()))
    return None, None, ("no_explicit_developmental_goal",)


def _goal_emphasis(goal: str | None) -> tuple[str | None, str | None, tuple[str, ...]]:
    mapping = {
        "develop_strength": ("maximum_strength_development", "aerobic_capacity_development", ("explicit_strength_development_goal",)),
        "improve_sprint_power": ("peak_power_development", "anaerobic_performance_development", ("explicit_sprint_power_goal",)),
        "sprint_1k": ("sprint_specific_preparation", "anaerobic_performance_development", ("explicit_sprint_race_goal",)),
        "head_5k": ("head_race_specific_preparation", "aerobic_strength_endurance", ("explicit_head_race_goal",)),
        "improve_2k": ("aerobic_capacity_development", "aerobic_strength_endurance", ("explicit_integrated_erg_goal",)),
        "build_endurance": ("aerobic_capacity_development", "technical_efficiency", ("explicit_endurance_goal",)),
        "technical_development": ("technical_efficiency", "aerobic_capacity_development", ("explicit_technical_goal",)),
        "build_consistency": ("foundational_general_preparation", "technical_efficiency", ("explicit_consistency_goal",)),
        "general_fitness": ("foundational_general_preparation", "aerobic_capacity_development", ("broad_general_fitness_goal",)),
        "race_preparation": ("race_specific_preparation_context", "technical_efficiency", ("explicit_race_preparation_goal",)),
    }
    return mapping.get(goal or "", (None, None, ()))


def _domain(profile: PhysiologicalEvidenceProfile, name: str):
    return next(item for item in profile.domains if item.domain == name)


def _assessment_candidates(
    *, evidence: PhysiologicalEvidenceProfile, emphasis: str, phase_type: str,
    training_environment: str, recovery_active: bool,
) -> tuple[AssessmentCandidate, ...]:
    """Select advisory questions, never age-triggered tests or scheduled work."""
    if recovery_active:
        return ()
    on_water_only = training_environment == "on_water"
    candidates: list[AssessmentCandidate] = []
    general_defer = ("active_recovery_or_recent_competition", "athlete_or_coach_defers_testing")
    integrated = _domain(evidence, "integrated_rowing_performance")
    aerobic_strength = _domain(evidence, "aerobic_strength_endurance")
    peak = _domain(evidence, "neuromuscular_peak_power")
    anaerobic = _domain(evidence, "anaerobic_performance")
    technical = _domain(evidence, "technical_efficiency")
    if on_water_only:
        if technical.evidence_status == "missing" or emphasis in {"technical_efficiency", "head_race_specific_preparation"}:
            candidates.append(AssessmentCandidate(
                "on_water_assessment", "on_water", "seasonal_transition_or_technical_context",
                "Establish modality-specific technical or pace context without fabricating an erg equivalent.",
                ("on_water_training_environment", "modality_specific_evidence"), phase_type, general_defer,
            ))
        return tuple(candidates)
    if emphasis in {"sprint_specific_preparation", "peak_power_development", "anaerobic_performance_development"}:
        if peak.evidence_status == "missing":
            candidates.append(AssessmentCandidate("seven_stroke_peak_power", "ergometer", "missing_complementary_evidence", "Clarify short-stroke power context for an explicit sprint/power goal.", ("goal_relevance", "not_a_deficit_diagnosis"), phase_type, general_defer))
        if anaerobic.evidence_status == "missing":
            candidates.append(AssessmentCandidate("sixty_second_maximal_effort", "ergometer", "missing_complementary_evidence", "Clarify sustained short-duration performance context for an explicit sprint/power goal.", ("goal_relevance", "not_a_deficit_diagnosis"), phase_type, general_defer))
    sustained_emphasis = {
        "head_race_specific_preparation", "aerobic_strength_endurance",
        "aerobic_capacity_development", "threshold_development_context",
    }
    relevant_foundational_question = (
        emphasis == "foundational_general_preparation"
        and integrated.evidence_status == "available"
        and any(item.protocol in {"sixty_second_maximal_effort", "seven_stroke_peak_power"} for item in evidence.measurements)
    )
    if emphasis in sustained_emphasis or relevant_foundational_question:
        if aerobic_strength.evidence_status == "missing":
            candidates.append(AssessmentCandidate("thirty_min_rate_capped_maximal", "ergometer", "baseline_or_missing_complementary_evidence", "A maximal fixed-rate R20 can provide sustained-performance context; it is not a measured threshold, CP, or VO2max.", ("goal_and_developmental_question_relevance", "optional_winter_erg_assessment"), phase_type, general_defer))
        elif emphasis in {"aerobic_capacity_development", "aerobic_strength_endurance", "head_race_specific_preparation"}:
            candidates.append(AssessmentCandidate("thirty_min_rate_capped_submaximal", "ergometer", "training_based_monitoring", "A submaximal fixed-rate R20 can support comparable monitoring when protocol conditions are repeatable.", ("monitoring_not_maximal_retest_deadline",), phase_type, general_defer, aerobic_strength.measurement_ids))
    if integrated.evidence_status == "missing" and emphasis in {"aerobic_capacity_development", "head_race_specific_preparation", "sprint_specific_preparation"}:
        candidates.append(AssessmentCandidate("two_k_time_trial", "ergometer", "baseline_assessment", "An optional integrated erg benchmark may clarify an explicit erg/race-development question.", ("goal_relevance", "not_required_in_on_water_season"), phase_type, general_defer))
    return tuple(candidates)


def _block_guidance(emphasis: str) -> DevelopmentalBlockGuidance:
    minimum, maximum, status = PROVISIONAL_DEVELOPMENTAL_BLOCK_GUIDANCE.get(
        emphasis, PROVISIONAL_DEVELOPMENTAL_BLOCK_GUIDANCE["foundational_general_preparation"],
    )
    return DevelopmentalBlockGuidance(
        emphasis, minimum, maximum, status,
        ("does_not_create_or_advance_a_block", "not_a_retesting_deadline", "requires_future_r9f6_persistent_block_state"),
    )


def build_development_recommendation(
    profile: Mapping[str, Any], *, evidence: PhysiologicalEvidenceProfile, planning_date: date,
    phase_context: Mapping[str, Any] | None = None, recovery_constraints: tuple[Any, ...] = (),
    current_training: Any | None = None, next_race_context: Mapping[str, Any] | None = None,
) -> DevelopmentRecommendation:
    """Build a transparent developmental recommendation without changing planning.

    The ordering mirrors the approved hierarchy: recovery/reconditioning,
    athlete goals, season context, training-history context, then available
    evidence.  It never reads the legacy ratio/limiter output.
    """
    phase = _mapping(phase_context)
    phase_type = str(phase.get("phase_type") or "general_preparation")
    athlete = _mapping(profile.get("athlete"))
    training_environment = str(athlete.get("training_environment") or "mixed")
    primary_goal, secondary_goal, goal_context = _goals(profile)
    recovery_active = bool(recovery_constraints) or phase_type in {"post_race_recovery", "race"}
    consistency = getattr(current_training, "consistency", None) or athlete.get("recent_training_consistency")
    history_source = getattr(getattr(current_training, "source", None), "value", None)
    history_context = [f"training_consistency:{consistency or 'unknown'}", f"current_training_evidence:{history_source or 'unknown'}"]
    recovery_context = ["active_recovery_constraint" if recovery_active else "no_active_recovery_constraint_detected"]
    rationale: list[str] = ["diagnostic_only_developmental_recommendation", "does_not_change_r2_r3_r4_r5_or_r6"]
    limitations = ["not_a_physiological_diagnosis", "no_heterogeneous_ratio_ranking", "no_automatic_readiness_adjustment"]
    recommendation_type = "foundational"
    decision_basis = "insufficient_specific_evidence_foundational_context"
    if recovery_active:
        primary, secondary = "recovery_reconditioning", None
        recommendation_type = "recovery_reconditioning"
        decision_basis = "recovery_context_supported"
        rationale.append("recovery_or_recent_competition_precedes_developmental_emphasis")
        limitations.append("defer_maximal_assessment_while_recovery_context_is_active")
    elif consistency in {"returning", "inconsistent", "variable"}:
        primary, secondary = "recovery_reconditioning", "foundational_general_preparation"
        recommendation_type = "reconditioning"
        decision_basis = "training_interruption_or_variable_tolerance_context"
        rationale.append("interrupted_or_variable_training_context_precedes_specific_development")
        limitations.append("training_tolerance_is_not_formally_assessed")
    else:
        primary, secondary, goal_rationale = _goal_emphasis(primary_goal)
        if primary:
            recommendation_type = "goal_and_season_driven"
            decision_basis = "goal_and_season_context_pending_evidence_review"
            rationale.extend(goal_rationale)
        elif phase_type in {"taper", "race_specific_preparation", "race"}:
            primary, secondary = "race_specific_preparation_context", "technical_efficiency"
            recommendation_type = "season_context_driven"
            decision_basis = "season_context_supported"
            rationale.append("season_and_race_context_guides_developmental_emphasis")
        else:
            primary, secondary = "foundational_general_preparation", "aerobic_capacity_development"
            recommendation_type = "foundational"
            decision_basis = "general_foundational_context"
            rationale.append("general_preparation_with_no_specific_physiological_deficit_claim")
    available = tuple(item for item in evidence.domains if item.evidence_status == "available")
    gaps = tuple(item.domain for item in evidence.domains if item.evidence_status == "missing")
    if not available:
        limitations.append("no_formal_performance_assessment_available")
    elif recommendation_type == "goal_and_season_driven":
        decision_basis = "goal_and_season_context_with_available_evidence"
    if evidence.coverage_description == "partial_domain_coverage_not_comprehensive":
        limitations.append("complementary_evidence_is_not_comprehensive_physiological_coverage")
    if training_environment == "on_water":
        rationale.append("on_water_training_environment_does_not_require_in_season_erg_testing")
    if not _mapping(next_race_context):
        rationale.append("no_upcoming_race_does_not_prevent_progressive_general_preparation")
    confidence = (
        "context_supported" if available and recommendation_type in {"goal_and_season_driven", "season_context_driven"}
        else "foundational_context_only" if recommendation_type == "foundational"
        else "cautious_contextual"
    )
    candidates = _assessment_candidates(
        evidence=evidence, emphasis=primary, phase_type=phase_type,
        training_environment=training_environment, recovery_active=recovery_active,
    )
    maintain = ("technical_efficiency", "low_intensity_aerobic_base", "training_tolerance_within_existing_policy")
    alternatives = ["coach_or_athlete_priorities_may_refine_emphasis", "future_r9f3c_can_revisit_when_new_valid_evidence_is_recorded"]
    if secondary_goal:
        alternatives.append(f"secondary_goal_consideration:{secondary_goal}")
    return DevelopmentRecommendation(
        RECOMMENDATION_VERSION, True, recommendation_type, decision_basis, primary, secondary, maintain,
        tuple(rationale), goal_context, (f"phase:{phase_type}", f"planning_date:{planning_date.isoformat()}"),
        tuple(history_context), tuple(recovery_context),
        tuple(item.measurement_id for item in evidence.measurements if item.validity != "invalid"), gaps,
        tuple(alternatives), confidence, tuple(limitations), candidates,
        ("new_valid_assessment_recorded", "goal_or_race_context_changes", "recovery_or_training_interruption_changes", "coach_or_athlete_updates_goal"),
        _block_guidance(primary),
        provenance=(RECOMMENDATION_VERSION, "typed_physiological_evidence", "profile_goal_and_season_context"),
    )
