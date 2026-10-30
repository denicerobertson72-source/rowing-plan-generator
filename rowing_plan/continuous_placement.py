"""Bounded, deterministic continuous placement for replacement-planner needs.

This is a parallel shadow-development layer.  It seeds factual authority,
searches chronological candidate dates with the M6 constraint evaluator, and
returns abstract placements only.  It has no live V2, API, persistence, or
workout-materialization consumer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any, Mapping

from .planning_domain import (
    AuthorityType,
    PhysiologicalCredit,
    PhysiologicalState,
    Placement,
    PlacementAuthorityStatus,
    ProjectedSequenceItem,
    ProjectedSequenceState,
    Provenance,
    ProvenanceSource,
    SequenceConstraintDecision,
    SequenceEligibility,
    TrainingNeed,
    TrainingRole,
)
from .planning_normalization import NormalizedPlanningContext
from .materialization_capabilities import minimum_materializable_reservation
from .sequence_constraints import evaluate_sequence_constraint
from .training_policy import DEFAULT_TRAINING_POLICY, POLICY_VERSION, TrainingPolicy


@dataclass(frozen=True)
class PlacementSearchConfig:
    """Software-only finite-search configuration, not training physiology."""

    horizon_days: int = 21
    beam_width: int = 24
    default_available_minutes: int = 120
    default_strength_reservation_minutes: int = 45
    default_noncredit_reservation_minutes: int = 30

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.beam_width < 1:
            raise ValueError("placement_search_config_must_be_positive")


@dataclass(frozen=True)
class AvailableDate:
    date: date
    available_minutes: int
    remaining_minutes: int
    multiple_sessions_allowed: bool
    prohibited_role_families: tuple[str, ...] = ()
    preferred: bool = False
    authoritative_placement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnplacedNeedDiagnostic:
    training_need_id: str
    status: str
    candidate_dates_considered: tuple[date, ...]
    primary_blocking_reason: str


@dataclass(frozen=True)
class ContinuousPlacementDiagnostics:
    objective_order: tuple[str, ...]
    search_width: int
    candidate_dates_considered: int
    authority_conflicts: tuple[str, ...] = ()
    unplaced_needs: tuple[UnplacedNeedDiagnostic, ...] = ()
    repeated_needs_placed_by_role: Mapping[str, int] = field(default_factory=dict)
    repeated_needs_unplaced_by_role: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "repeated_needs_placed_by_role", MappingProxyType(dict(sorted(self.repeated_needs_placed_by_role.items()))))
        object.__setattr__(self, "repeated_needs_unplaced_by_role", MappingProxyType(dict(sorted(self.repeated_needs_unplaced_by_role.items()))))


@dataclass(frozen=True)
class ContinuousPlacementResult:
    placements: tuple[Placement, ...]
    projected_sequence: ProjectedSequenceState
    available_dates: tuple[AvailableDate, ...]
    diagnostics: ContinuousPlacementDiagnostics


@dataclass(frozen=True)
class _SearchState:
    placements: tuple[Placement, ...]
    projected: ProjectedSequenceState
    remaining: Mapping[date, int]
    placed_need_ids: tuple[str, ...]
    fulfillment: int = 0
    soft_penalty: int = 0
    preference_gain: int = 0
    stability_gain: int = 0
    density_penalty: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "remaining", MappingProxyType(dict(sorted(self.remaining.items()))))


_OBJECTIVE_ORDER = (
    "hard_constraint_violations",
    "authoritative_commitment_preservation",
    "recovery_and_spacing",
    "race_and_taper_readiness",
    "high_priority_need_fulfillment",
    "protected_aerobic_and_strength_minima",
    "progression_quality",
    "excessive_density",
    "athlete_preference",
    "plan_stability",
    "deterministic_date_need_tie_break",
)
_PRIORITY_WEIGHT = {
    "race_readiness": 90,
    "race_specific": 85,
    "quality_development": 80,
    "protected_aerobic_base": 70,
    "aerobic_target_topup": 55,
    "required_strength_minimum": 75,
    "strength_taper_maintenance": 64,
    "strength_maintenance": 65,
    "aerobic_base": 60,
    "recovery": 55,
}
_ROWING_ROLES = frozenset({
    TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC,
    TrainingRole.UT1_AEROBIC_STRENGTH, TrainingRole.QUALITY,
    TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR,
    TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP,
    TrainingRole.TECHNICAL_REENTRY, TrainingRole.RECOVERY_AEROBIC,
    TrainingRole.TAPER_MAINTENANCE, TrainingRole.RACE_PREPARATION,
    TrainingRole.RACE, TrainingRole.COURSE_PRACTICE,
})


def _priority(need: TrainingNeed) -> int:
    return _PRIORITY_WEIGHT.get(need.priority, 50)


def _role_family(role: TrainingRole) -> str:
    if role in _ROWING_ROLES:
        return "rowing"
    if role is TrainingRole.STRENGTH:
        return "strength"
    return role.value


def _date_item(profile: Mapping[str, Any] | None, day: date, config: PlacementSearchConfig) -> Mapping[str, Any]:
    profile = profile or {}
    specific = profile.get("date_availability", ()) if isinstance(profile, Mapping) else ()
    if isinstance(specific, Mapping):
        value = specific.get(day.isoformat())
        if isinstance(value, Mapping):
            return value
    elif isinstance(specific, (list, tuple)):
        for value in specific:
            if isinstance(value, Mapping) and str(value.get("date")) == day.isoformat():
                return value
    weekly = profile.get("weekly_availability", ()) if isinstance(profile, Mapping) else ()
    for value in weekly if isinstance(weekly, (list, tuple)) else ():
        if isinstance(value, Mapping) and str(value.get("weekday", "")).lower() == day.strftime("%A").lower():
            return value
    return {"available": True, "max_training_minutes": config.default_available_minutes}


def _availability(
    context: NormalizedPlanningContext,
    profile: Mapping[str, Any] | None,
    config: PlacementSearchConfig,
) -> tuple[dict[date, AvailableDate], tuple[Placement, ...], ProjectedSequenceState, tuple[str, ...]]:
    end = context.planning_date + timedelta(days=config.horizon_days - 1)
    dates: dict[date, AvailableDate] = {}
    for offset in range(config.horizon_days):
        day = context.planning_date + timedelta(days=offset)
        item = _date_item(profile, day, config)
        available = bool(item.get("available", True))
        minutes = max(0, int(item.get("max_training_minutes", config.default_available_minutes))) if available else 0
        restrictions = tuple(sorted(str(value) for value in item.get("prohibited_role_families", ())))
        dates[day] = AvailableDate(
            day, minutes, minutes, bool(item.get("multiple_sessions_allowed", item.get("allow_multiple_sessions", True))),
            restrictions, bool(item.get("preferred", False)), (),
        )

    seed: list[Placement] = []
    projected = ProjectedSequenceState()
    authority_conflicts: list[str] = []
    priorities = {item[0]: item[1] for item in context.race_context}
    for commitment in context.authoritative_commitments:
        if commitment.date is None or commitment.date not in dates:
            continue
        role = {
            AuthorityType.RACE: TrainingRole.RACE,
            AuthorityType.COURSE_PRACTICE: TrainingRole.COURSE_PRACTICE,
            AuthorityType.PRIVATE_COACHING: TrainingRole.PRIVATE_COACHING,
            AuthorityType.FIXED_COACHING: TrainingRole.COACHED_TRAINING,
            AuthorityType.FIXED_RECURRING_ACTIVITY: TrainingRole.COACHED_TRAINING,
            AuthorityType.FIXED_REST: TrainingRole.REST,
        }.get(commitment.type, TrainingRole.COACHED_TRAINING)
        credit = PhysiologicalCredit(TrainingRole.RACE, 1, commitment.reservation_minutes, True) if role is TrainingRole.RACE else PhysiologicalCredit()
        priority = priorities.get(commitment.date.isoformat(), "B")
        source = f"race:{priority}" if role is TrainingRole.RACE else f"authority:{commitment.type.value}"
        placement_id = f"authority:{commitment.id}"
        seed.append(Placement(
            placement_id=placement_id, training_need_id=f"authority:{commitment.id}", date=commitment.date,
            reservation_minutes=commitment.reservation_minutes, target_credit=credit,
            provenance=commitment.provenance, authority_status=PlacementAuthorityStatus.AUTHORITATIVE,
            role=role,
            policy_rule_ids=(), selection_reason="authoritative_commitment_preserved",
            served_need_ids=(), authority_type=commitment.type,
        ))
        projected = projected.with_item(ProjectedSequenceItem(
            placement_id, commitment.date, role, credit, authoritative=True, source=source,
        ))
        current = dates[commitment.date]
        remaining = current.remaining_minutes - commitment.reservation_minutes
        if remaining < 0:
            authority_conflicts.append(f"authority_exceeds_capacity:{commitment.id}")
        dates[commitment.date] = AvailableDate(
            current.date, current.available_minutes, max(0, remaining), current.multiple_sessions_allowed,
            current.prohibited_role_families, current.preferred,
            tuple(sorted((*current.authoritative_placement_ids, placement_id))),
        )
    return dates, tuple(sorted(seed, key=lambda item: (item.date, item.placement_id))), projected, tuple(sorted(authority_conflicts))


def _reservation_minutes(need: TrainingNeed, decision: SequenceConstraintDecision, config: PlacementSearchConfig,
                         *, profile: Mapping[str, Any] | None, day: date) -> int | None:
    credit = decision.recommended_credit_adjustment or need.target_credit
    minimum, _selector_role = minimum_materializable_reservation(
        role=need.role.value, profile=profile or {}, day=day,
    )
    if minimum is None:
        return None
    if credit.minutes:
        return max(credit.minutes, minimum)
    if need.role is TrainingRole.STRENGTH:
        return max(config.default_strength_reservation_minutes, minimum)
    return max(config.default_noncredit_reservation_minutes, minimum)


def _placement_score(need: TrainingNeed, decision: SequenceConstraintDecision, day: AvailableDate, stable: bool) -> tuple[int, int, int, int]:
    decision_bonus = {
        SequenceEligibility.ELIGIBLE: 4,
        SequenceEligibility.ELIGIBLE_REDUCED: 3,
        SequenceEligibility.ELIGIBLE_LOW_COST_ONLY: 2,
        SequenceEligibility.DISCOURAGED: 1,
    }[decision.eligibility]
    later_horizon_distribution = int(
        bool(need.progression_relation and "load_target_residual" in need.progression_relation and ":14d" in need.progression_relation)
        and day.date >= need.earliest_date + timedelta(days=7)
    )
    return (_priority(need) * 10 + decision_bonus, -len(decision.soft_constraints), int(day.preferred) + later_horizon_distribution, int(stable))


def _state_key(state: _SearchState) -> tuple[Any, ...]:
    # Higher is better until the final canonical chronological tie-break.
    # The search sorts descending, so negate ordinals and character codes to
    # keep the final tie-break chronological then lexical, rather than making
    # a later date win merely as an artefact of reverse sorting.
    canonical = tuple(
        (-item.date.toordinal(), tuple(-ord(char) for char in item.placement_id))
        for item in state.placements
    )
    return (
        state.fulfillment,
        -state.soft_penalty,
        state.preference_gain,
        state.stability_gain,
        -state.density_penalty,
        canonical,
    )


def _placement_provenance(need: TrainingNeed, policy: TrainingPolicy) -> Provenance:
    return Provenance(
        source_object_id=need.id, source_type=ProvenanceSource.LEGACY_V2,
        source_policy_version=policy.version or POLICY_VERSION,
        original_serialized_identity=(need.id,), adaptation_notes=("continuous_sequence_placement",),
    )


def _served_need_ids(need: TrainingNeed, all_needs: tuple[TrainingNeed, ...]) -> tuple[str, ...]:
    if need.role is not TrainingRole.LONG_AEROBIC or need.progression_relation != "may_contribute:dedicated_ut2":
        return (need.id,)
    ut2 = [item.id for item in all_needs if item.role is TrainingRole.DEDICATED_UT2]
    return tuple(sorted((need.id, *ut2)))


def _can_use_date(day: AvailableDate, need: TrainingNeed, reservation: int, existing: tuple[Placement, ...]) -> str | None:
    if _role_family(need.role) in day.prohibited_role_families:
        return "activity_prohibited"
    if reservation > day.remaining_minutes:
        return "insufficient_capacity"
    date_placements = [item for item in existing if item.date == day.date]
    if any(item.role is TrainingRole.REST for item in date_placements):
        return "designated_rest"
    if any(_role_family(item.role) == _role_family(need.role) for item in date_placements if item.role is not None):
        return "same_role_family_already_placed"
    if date_placements and not day.multiple_sessions_allowed:
        return "multiple_sessions_not_allowed"
    return None


def _candidate_days(need: TrainingNeed, dates: Mapping[date, AvailableDate]) -> tuple[AvailableDate, ...]:
    return tuple(item for day, item in sorted(dates.items()) if need.earliest_date <= day <= need.latest_date)


def _unplaced(
    context: NormalizedPlanningContext,
    state: PhysiologicalState,
    needs: tuple[TrainingNeed, ...],
    dates: Mapping[date, AvailableDate],
    final: _SearchState,
    policy: TrainingPolicy,
    config: PlacementSearchConfig,
    profile: Mapping[str, Any] | None,
) -> tuple[UnplacedNeedDiagnostic, ...]:
    result = []
    placed = set(final.placed_need_ids)
    for need in needs:
        if need.id in placed:
            continue
        candidates = _candidate_days(need, dates)
        reasons: list[str] = []
        for day in candidates:
            decision = evaluate_sequence_constraint(context, state, need, day.date, policy, projected=final.projected)
            if decision.eligibility is SequenceEligibility.PROHIBITED:
                reasons.extend(decision.hard_rejection_reasons)
                continue
            final_day = AvailableDate(
                day.date, day.available_minutes, final.remaining[day.date],
                day.multiple_sessions_allowed, day.prohibited_role_families,
                day.preferred, day.authoritative_placement_ids,
            )
            reservation = _reservation_minutes(need, decision, config, profile=profile, day=day.date)
            if reservation is None:
                reasons.append("no_materializable_catalog_envelope")
                continue
            reason = _can_use_date(final_day, need, reservation, final.placements)
            if reason:
                reasons.append(reason)
        if need.latest_date < context.planning_date:
            status, primary = "missed", "window_elapsed"
        elif not candidates:
            status, primary = "deferred", "outside_search_horizon"
        elif reasons and all("recovery" in item or "spacing" in item for item in reasons):
            status, primary = "blocked_by_recovery", sorted(reasons)[0]
        elif "insufficient_capacity" in reasons or "multiple_sessions_not_allowed" in reasons:
            status, primary = "blocked_by_capacity", "insufficient_capacity" if "insufficient_capacity" in reasons else "multiple_sessions_not_allowed"
        elif reasons:
            status, primary = "blocked", sorted(reasons)[0]
        else:
            status, primary = "deferred", "left_empty_is_preferred"
        result.append(UnplacedNeedDiagnostic(need.id, status, tuple(item.date for item in candidates), primary))
    return tuple(sorted(result, key=lambda item: item.training_need_id))


def place_continuous_training_needs(
    context: NormalizedPlanningContext,
    state: PhysiologicalState,
    needs: tuple[TrainingNeed, ...] | list[TrainingNeed],
    policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
    *,
    profile: Mapping[str, Any] | None = None,
    config: PlacementSearchConfig = PlacementSearchConfig(),
    prior_provisional_dates: Mapping[str, date] | None = None,
) -> ContinuousPlacementResult:
    """Place abstract needs with bounded deterministic beam search.

    Search ordering is a software concern.  The M6 evaluator remains the sole
    authority on physiological eligibility.
    """
    if state.as_of_date != context.planning_date:
        raise ValueError("physiological_state_planning_date_mismatch")
    all_needs = tuple(sorted(needs, key=lambda item: (-_priority(item), item.id)))
    dates, seeded, projected, authority_conflicts = _availability(context, profile, config)
    initial = _SearchState(
        placements=seeded, projected=projected,
        remaining={item.date: item.remaining_minutes for item in dates.values()},
        placed_need_ids=(),
    )
    states = (initial,)
    prior = prior_provisional_dates or {}

    for need in all_needs:
        branches: list[_SearchState] = []
        for current in states:
            # Empty/deferred is always a real option and carries no cosmetic penalty.
            branches.append(current)
            for available in _candidate_days(need, dates):
                day = AvailableDate(
                    available.date, available.available_minutes, current.remaining[available.date],
                    available.multiple_sessions_allowed, available.prohibited_role_families,
                    available.preferred, available.authoritative_placement_ids,
                )
                decision = evaluate_sequence_constraint(context, state, need, day.date, policy, projected=current.projected)
                if decision.eligibility is SequenceEligibility.PROHIBITED:
                    continue
                reservation = _reservation_minutes(need, decision, config, profile=profile, day=day.date)
                if reservation is None:
                    continue
                unavailable_reason = _can_use_date(day, need, reservation, current.placements)
                if unavailable_reason:
                    continue
                stable = prior.get(need.id) == day.date
                score, soft, preference, stability = _placement_score(need, decision, day, stable)
                placement_id = f"placement:{need.id}:{day.date.isoformat()}"
                placement = Placement(
                    placement_id=placement_id, training_need_id=need.id, date=day.date,
                    reservation_minutes=reservation, target_credit=decision.recommended_credit_adjustment or need.target_credit,
                    provenance=_placement_provenance(need, policy), authority_status=PlacementAuthorityStatus.PROVISIONAL,
                    role=need.role,
                    applied_constraint_decisions=decision.diagnostic_codes, constraint_decision=decision,
                    policy_rule_ids=decision.policy_rule_ids, preference_effect="date_preferred" if day.preferred else None,
                    stability_effect="prior_provisional_date_preserved" if stable else None,
                    selection_reason=f"selected_by_continuous_sequence:{decision.eligibility.value}",
                    served_need_ids=_served_need_ids(need, all_needs),
                )
                remaining = dict(current.remaining)
                remaining[day.date] -= reservation
                projected_item = ProjectedSequenceItem(
                    placement_id, day.date, need.role, placement.target_credit, source="provisional",
                )
                density = sum(item.date == day.date for item in current.placements)
                # Soft continuity only: do not override recovery, authority,
                # availability, or hard sequence decisions.  A legal aerobic
                # choice is less attractive when it needlessly neighbours a
                # same-family aerobic placement; the deterministic tie-break
                # no longer rewards early clustering by default.
                adjacent_aerobic = sum(
                    item.role in {TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC, TrainingRole.RECOVERY_AEROBIC}
                    and need.role in {TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC, TrainingRole.RECOVERY_AEROBIC}
                    and abs((item.date - day.date).days) == 1
                    for item in current.placements
                )
                branches.append(_SearchState(
                    placements=tuple(sorted((*current.placements, placement), key=lambda item: (item.date, item.placement_id))),
                    projected=current.projected.with_item(projected_item), remaining=remaining,
                    placed_need_ids=tuple(sorted((*current.placed_need_ids, need.id))),
                    fulfillment=current.fulfillment + score,
                    soft_penalty=current.soft_penalty + soft,
                    preference_gain=current.preference_gain + preference,
                    stability_gain=current.stability_gain + stability,
                    density_penalty=current.density_penalty + density + adjacent_aerobic,
                ))
        states = tuple(sorted(branches, key=_state_key, reverse=True)[:config.beam_width])

    final = sorted(states, key=_state_key, reverse=True)[0]
    available_dates = tuple(
        AvailableDate(item.date, item.available_minutes, final.remaining[item.date], item.multiple_sessions_allowed,
                      item.prohibited_role_families, item.preferred, item.authoritative_placement_ids)
        for item in sorted(dates.values(), key=lambda item: item.date)
    )
    unplaced = _unplaced(context, state, all_needs, dates, final, policy, config, profile)
    need_by_id = {item.id: item for item in all_needs}
    repeated_placed: dict[str, int] = {}
    repeated_unplaced: dict[str, int] = {}
    for need_id in final.placed_need_ids:
        need = need_by_id[need_id]
        if need.exposure_count > 1:
            repeated_placed[need.role.value] = repeated_placed.get(need.role.value, 0) + 1
    for diagnostic in unplaced:
        need = need_by_id[diagnostic.training_need_id]
        if need.exposure_count > 1:
            repeated_unplaced[need.role.value] = repeated_unplaced.get(need.role.value, 0) + 1
    return ContinuousPlacementResult(
        placements=final.placements, projected_sequence=final.projected, available_dates=available_dates,
        diagnostics=ContinuousPlacementDiagnostics(
            objective_order=_OBJECTIVE_ORDER, search_width=config.beam_width,
            candidate_dates_considered=sum(len(_candidate_days(need, dates)) for need in all_needs),
            authority_conflicts=authority_conflicts, unplaced_needs=unplaced,
            repeated_needs_placed_by_role=repeated_placed,
            repeated_needs_unplaced_by_role=repeated_unplaced,
        ),
    )
