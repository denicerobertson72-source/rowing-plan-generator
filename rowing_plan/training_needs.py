"""Pure, continuous TrainingNeed generation for the replacement planner.

This module describes the next useful training stimuli and their chronological
windows.  It does not choose dates, inspect prior provisional calendar shape,
or feed the live Scheduler V2 placement path.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any, Mapping, Iterable

from .physiological_state import reduce_physiological_state
from .planning_domain import (
    AuthorityType,
    PhysiologicalCredit,
    PhysiologicalState,
    TrainingLoadTarget,
    TrainingDistributionTarget,
    TrainingNeed,
    TrainingRole,
)
from .planning_normalization import NormalizedPlanningContext
from .training_load_targets import reconcile_training_load_targets
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy, TrainingPolicyRule


_QUALITY_ROLES = frozenset({
    TrainingRole.THRESHOLD_AT,
    TrainingRole.RACE_SPECIFIC_TR,
    TrainingRole.ANAEROBIC_AN,
    TrainingRole.POWER_PP,
})
_HARD_RECOVERY_ROLES = _QUALITY_ROLES | {TrainingRole.STRENGTH}
_NEED_STATES = frozenset({"active", "satisfied", "partially_satisfied", "deferred", "superseded", "reduced", "missed"})


@dataclass(frozen=True)
class TrainingNeedDiagnostics:
    active_need_count_by_role: Mapping[str, int] = field(default_factory=dict)
    needs_deferred: tuple[str, ...] = ()
    needs_reduced: tuple[str, ...] = ()
    needs_superseded: tuple[str, ...] = ()
    needs_blocked_by_recovery: tuple[str, ...] = ()
    race_taper_adjustments: tuple[str, ...] = ()
    source_policy_ids: tuple[str, ...] = ()
    repeated_needs_generated_by_role: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "active_need_count_by_role", MappingProxyType(dict(sorted(self.active_need_count_by_role.items()))))
        object.__setattr__(self, "repeated_needs_generated_by_role", MappingProxyType(dict(sorted(self.repeated_needs_generated_by_role.items()))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_need_count_by_role": dict(self.active_need_count_by_role),
            "needs_deferred": list(self.needs_deferred),
            "needs_reduced": list(self.needs_reduced),
            "needs_superseded": list(self.needs_superseded),
            "needs_blocked_by_recovery": list(self.needs_blocked_by_recovery),
            "race_taper_adjustments": list(self.race_taper_adjustments),
            "source_policy_ids": list(self.source_policy_ids),
            "repeated_needs_generated_by_role": dict(self.repeated_needs_generated_by_role),
        }


@dataclass(frozen=True)
class TrainingNeedGeneration:
    needs: tuple[TrainingNeed, ...]
    diagnostics: TrainingNeedDiagnostics


@dataclass(frozen=True)
class FrequencyRedistributionDiagnostic:
    """Audit record for a budget-neutral physical exposure partition.

    This is deliberately about implementation mechanics, not a new
    physiological target.  Only a residual low-intensity credit with explicit
    ``load_target_residual`` provenance is eligible for partitioning.
    """

    distribution_target_exposures: int
    selected_exposures: int
    frequency_deficit: int
    source_need_id: str | None = None
    original_credit: int = 0
    partition_credits: tuple[int, ...] = ()
    redistribution_attempted: bool = False
    redistribution_reason: str | None = None
    redistribution_blocked_reason: str | None = None
    total_credit_conserved: bool = True


@dataclass(frozen=True)
class FrequencyRedistributionResult:
    needs: tuple[TrainingNeed, ...]
    diagnostics: tuple[FrequencyRedistributionDiagnostic, ...]


def validate_frequency_partitions(needs: Iterable[TrainingNeed]) -> None:
    """Reject malformed parent/child credit graphs before placement."""
    grouped: dict[str, list[TrainingNeed]] = {}
    for item in needs:
        if item.partition_reason != "taper_frequency_redistribution":
            continue
        if not item.parent_need_id or item.original_target_credit_minutes is None:
            raise ValueError("taper_frequency_partition_metadata_missing")
        grouped.setdefault(item.parent_need_id, []).append(item)
    for parent, children in grouped.items():
        original = {item.original_target_credit_minutes for item in children}
        if len(original) != 1 or sum(item.target_credit.minutes for item in children) != next(iter(original)):
            raise ValueError(f"taper_frequency_partition_credit_not_conserved:{parent}")


def transition_training_need(need: TrainingNeed, state: str, reason: str) -> TrainingNeed:
    """Represent a lifecycle transition without recreating a weekly duty."""
    if state not in _NEED_STATES:
        raise ValueError("training_need_transition_is_invalid")
    return replace(need, state=state, state_reason=reason)


def _rules(policy: TrainingPolicy) -> dict[str, TrainingPolicyRule]:
    return {rule.key: rule for rule in policy.rules}


def _integer(value: Any) -> int:
    return max(0, int(value))


def _race_context(context: NormalizedPlanningContext, state: PhysiologicalState) -> dict[str, Any]:
    if state.next_race_context:
        return dict(state.next_race_context)
    races = [item for item in context.authoritative_commitments if item.type is AuthorityType.RACE and item.date]
    if not races:
        return {}
    race = min(races, key=lambda item: (item.date, item.id))
    return {"date": race.date.isoformat(), "days_until": (race.date - context.planning_date).days, "priority": "B"}


def _phase(state: PhysiologicalState, race: Mapping[str, Any]) -> str:
    phase = str(state.phase_context.get("phase_type") or "")
    if phase:
        return phase
    days = race.get("days_until")
    if isinstance(days, int) and days <= 21:
        return "race_specific_preparation"
    return "general_preparation"


def _capacity_credit(profile: Mapping[str, Any] | None, baseline: int) -> int:
    """Use supplied athlete capacity only to cap a magnitude, never a weekday."""
    athlete = (profile or {}).get("athlete", {}) if isinstance(profile, Mapping) else {}
    supplied = athlete.get("current_approx_weekly_rowing_minutes")
    if supplied is None:
        return baseline
    return min(baseline, max(0, _integer(supplied) // 3))


def _window(
    planning_date: date,
    horizon_days: int,
    race: Mapping[str, Any],
    *,
    earliest: date | None = None,
) -> tuple[date, date]:
    start = earliest or planning_date
    end = planning_date + timedelta(days=max(0, horizon_days - 1))
    race_day = race.get("date")
    if isinstance(race_day, str):
        parsed = date.fromisoformat(race_day)
        if parsed > start:
            end = min(end, parsed - timedelta(days=1))
    return start, max(start, end)


def _need_id(policy: TrainingPolicy, role: TrainingRole, relation: str | None) -> str:
    return f"need:{policy.version}:{role.value}:{relation or 'ordinary'}"


def _need(
    policy: TrainingPolicy,
    role: TrainingRole,
    credit: PhysiologicalCredit,
    window: tuple[date, date],
    *,
    recovery_cost: str,
    priority: str,
    policy_keys: tuple[str, ...],
    race_relevance: str | None = None,
    progression_relation: str | None = None,
    state: str = "active",
    state_reason: str | None = None,
    provenance: tuple[str, ...] = ("physiological_state",),
) -> TrainingNeed:
    return TrainingNeed(
        id=_need_id(policy, role, progression_relation), role=role, target_credit=credit,
        earliest_date=window[0], latest_date=window[1], recovery_cost=recovery_cost,
        priority=priority, source_policy="|".join(sorted(policy_keys)),
        race_relevance=race_relevance, progression_relation=progression_relation,
        provenance=tuple(sorted(provenance)), state=state, state_reason=state_reason,
    )


def expand_repeated_exposure_needs(needs: list[TrainingNeed] | tuple[TrainingNeed, ...]) -> tuple[TrainingNeed, ...]:
    """Expand a multi-exposure deficit into independently placeable needs.

    A placement represents one physical exposure.  Keeping three exposures in
    one ``TrainingNeed`` would let the solver satisfy all three with one date.
    Minutes, if present, are split deterministically and exactly preserve the
    parent total.
    """
    result: list[TrainingNeed] = []
    for need in needs:
        count = need.target_credit.exposures
        if count <= 1:
            result.append(need)
            continue
        minutes, remainder = divmod(need.target_credit.minutes, count)
        for index in range(1, count + 1):
            credit = PhysiologicalCredit(
                role=need.target_credit.role,
                exposures=1,
                minutes=minutes + int(index <= remainder),
                eligible=need.target_credit.eligible,
            )
            result.append(replace(
                need,
                id=f"{need.id}:exposure:{index:02d}",
                target_credit=credit,
                parent_need_id=need.id,
                exposure_index=index,
                exposure_count=count,
                provenance=tuple(sorted((*need.provenance, "atomic_repeated_exposure"))),
            ))
    return tuple(sorted(result, key=lambda item: (item.earliest_date, item.latest_date, item.id)))


_LOW_INTENSITY_ROLES = frozenset({
    TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC,
    TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY,
})


def _availability_allows(profile: Mapping[str, Any], day: date) -> bool:
    for item in profile.get("date_availability", ()):
        if isinstance(item, Mapping) and str(item.get("date")) == day.isoformat():
            return bool(item.get("available", True))
    for item in profile.get("weekly_availability", ()):
        if isinstance(item, Mapping) and str(item.get("weekday", "")).lower() == day.strftime("%A").lower():
            return bool(item.get("available", True))
    return True


def _is_fixed_rest(context: NormalizedPlanningContext, day: date) -> bool:
    return any(item.type is AuthorityType.FIXED_REST and item.date == day for item in context.authoritative_commitments)


def _pre_race_movement_dates(context: NormalizedPlanningContext, need: TrainingNeed) -> tuple[date, ...]:
    race_days = tuple(sorted(date.fromisoformat(item[0]) for item in context.race_context))
    values = []
    current = need.earliest_date
    while current <= need.latest_date:
        # The frequency preference is deliberately weaker in the final 48h;
        # do not create a partition solely for that window.
        if not any(0 <= (race_day - current).days <= 2 for race_day in race_days):
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def partition_taper_frequency_needs(
    needs: Iterable[TrainingNeed],
    distribution_targets: Iterable[TrainingDistributionTarget],
    *,
    context: NormalizedPlanningContext,
    state: PhysiologicalState,
    profile: Mapping[str, Any],
    selected_placements=(),
    load_targets: Iterable[TrainingLoadTarget] = (),
) -> FrequencyRedistributionResult:
    """Split eligible *residual* low-intensity credit into two sessions.

    A residual is generic load-top-up credit, unlike a categorical dedicated
    UT2 requirement.  The retained UT2 child therefore protects that
    physiological purpose while the technical child can supply low-cost taper
    movement.  No parent remains once children are emitted.
    """
    from .materialization_capabilities import minimum_materializable_reservation

    original = tuple(needs)
    targets = tuple(item for item in distribution_targets if item.taper_frequency_retention)
    load_by_horizon = {item.horizon_days: item for item in load_targets}
    if not targets:
        return FrequencyRedistributionResult(original, ())

    selected = tuple(selected_placements)
    diagnostics: list[FrequencyRedistributionDiagnostic] = []
    result = list(original)
    already_partitioned = False
    for target in targets:
        load_target = load_by_horizon.get(target.horizon_days)
        if load_target and load_target.completed_rowing_minutes >= load_target.total_rowing_target_minutes:
            diagnostics.append(FrequencyRedistributionDiagnostic(
                target.target_rowing_exposures, 0, 0,
                redistribution_blocked_reason="recent_load_target_satisfied",
            ))
            continue
        in_horizon = tuple(
            item for item in selected
            if target.horizon_start <= item.date <= target.horizon_end
            and item.role in _LOW_INTENSITY_ROLES
        )
        rowing_selected = tuple(
            item for item in selected
            if target.horizon_start <= item.date <= target.horizon_end
            and item.role in (_LOW_INTENSITY_ROLES | {
                TrainingRole.UT1_AEROBIC_STRENGTH, TrainingRole.QUALITY,
                TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR,
                TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP,
                TrainingRole.TAPER_MAINTENANCE, TrainingRole.RACE_PREPARATION,
                TrainingRole.RACE, TrainingRole.COURSE_PRACTICE,
                TrainingRole.PRIVATE_COACHING, TrainingRole.COACHED_TRAINING,
            })
        )
        # Each source need is one physical exposure before any split.
        candidates = tuple(
            item for item in result
            if item.role is TrainingRole.DEDICATED_UT2
            and item.target_credit.eligible
            and item.target_credit.exposures == 1
            and item.progression_relation and "load_target_residual" in item.progression_relation
            and target.horizon_start <= item.earliest_date <= target.horizon_end
        )
        selected_exposures = len(rowing_selected)
        deficit = max(
            0,
            target.target_rowing_exposures - selected_exposures,
            target.minimum_low_intensity_exposures - len(in_horizon),
        )
        if not deficit:
            diagnostics.append(FrequencyRedistributionDiagnostic(
                target.target_rowing_exposures, selected_exposures, 0,
                redistribution_blocked_reason="frequency_target_satisfied",
            ))
            continue
        if already_partitioned:
            diagnostics.append(FrequencyRedistributionDiagnostic(
                target.target_rowing_exposures, selected_exposures, deficit,
                redistribution_blocked_reason="one_partition_is_sufficient_for_current_pass",
            ))
            continue
        source = next((item for item in candidates if not any(
            constraint.start_date <= item.latest_date
            and constraint.end_date >= item.earliest_date
            and {TrainingRole.DEDICATED_UT2, TrainingRole.TECHNICAL_REENTRY} <= set(constraint.hard_excluded_roles)
            for constraint in state.recovery_constraints
        )), None)
        if source is None:
            diagnostics.append(FrequencyRedistributionDiagnostic(
                target.target_rowing_exposures, selected_exposures, deficit,
                redistribution_blocked_reason="hard_recovery_or_no_redistributable_credit",
            ))
            continue
        viable_days = tuple(
            item for item in _pre_race_movement_dates(context, source)
            if _availability_allows(profile, item) and not _is_fixed_rest(context, item)
        )
        if not viable_days:
            diagnostics.append(FrequencyRedistributionDiagnostic(
                target.target_rowing_exposures, selected_exposures, deficit,
                source.id, source.target_credit.minutes,
                redistribution_blocked_reason="race_proximity_or_availability",
            ))
            continue
        dedicated_min, _ = minimum_materializable_reservation(role="dedicated_ut2", profile=profile, day=viable_days[0])
        technical_min, _ = minimum_materializable_reservation(role="technical_reentry", profile=profile, day=viable_days[0])
        if dedicated_min is None or technical_min is None or source.target_credit.minutes < dedicated_min + technical_min:
            diagnostics.append(FrequencyRedistributionDiagnostic(
                target.target_rowing_exposures, selected_exposures, deficit,
                source.id, source.target_credit.minutes,
                redistribution_blocked_reason="insufficient_redistributable_credit_for_catalog_minimum",
            ))
            continue
        technical_credit = min(source.target_credit.minutes - dedicated_min, max(technical_min, source.target_credit.minutes // 2))
        dedicated_credit = source.target_credit.minutes - technical_credit
        first = replace(
            source, id=f"{source.id}:partition:01", target_credit=PhysiologicalCredit(
                TrainingRole.DEDICATED_UT2, 1, dedicated_credit, True,
            ), parent_need_id=source.id, exposure_index=1, exposure_count=2,
            original_target_credit_minutes=source.target_credit.minutes,
            partition_reason="taper_frequency_redistribution",
            provenance=tuple(sorted((*source.provenance, "taper_frequency_redistribution"))),
        )
        second = replace(
            source, id=f"{source.id}:partition:02", role=TrainingRole.TECHNICAL_REENTRY,
            target_credit=PhysiologicalCredit(TrainingRole.TECHNICAL_REENTRY, 1, technical_credit, True),
            parent_need_id=source.id, exposure_index=2, exposure_count=2,
            original_target_credit_minutes=source.target_credit.minutes,
            partition_reason="taper_frequency_redistribution",
            provenance=tuple(sorted((*source.provenance, "taper_frequency_redistribution"))),
        )
        if first.target_credit.minutes + second.target_credit.minutes != source.target_credit.minutes:
            raise ValueError("taper_frequency_partition_credit_not_conserved")
        result.remove(source)
        result.extend((first, second))
        diagnostics.append(FrequencyRedistributionDiagnostic(
            target.target_rowing_exposures, selected_exposures, deficit,
            source.id, source.target_credit.minutes,
            (first.target_credit.minutes, second.target_credit.minutes), True,
            redistribution_reason="taper_frequency_redistribution",
            total_credit_conserved=True,
        ))
        already_partitioned = True
    return FrequencyRedistributionResult(
        tuple(sorted(result, key=lambda item: (item.earliest_date, item.latest_date, item.id))),
        tuple(diagnostics),
    )


def generate_residual_aerobic_needs(
    needs: tuple[TrainingNeed, ...], targets: tuple[TrainingLoadTarget, ...], policy: TrainingPolicy,
    *, selected_placements=None,
) -> tuple[TrainingNeed, ...]:
    """Add only low-intensity work required by an unsatisfied load target.

    Categorical needs are reconciled first.  This intentionally cannot create
    quality work: a total-load deficit is met with aerobic work or remains an
    explicit unfilled deficit when no meaningful aerobic exposure is needed.
    """
    rules = _rules(policy)
    minimum = int(rules["residual_aerobic_session_minutes"].value["minimum"])
    preferred = int(rules["residual_aerobic_session_minutes"].value["preferred"])
    generated = list(needs)
    need_by_id = {item.id: item for item in needs}
    reconciled_selected = (
        reconcile_training_load_targets(targets, needs)
        if selected_placements is None
        else reconcile_training_load_targets(targets, placements=selected_placements, need_by_id=need_by_id)
    )
    selected_by_id = {item.id: item for item in reconciled_selected}
    credited_to_later_targets: dict[str, int] = {}
    # Resolve short target deficits first only for *credit accounting*.  The
    # later target receives that same physical credit, so it emits only the
    # additional dose it still needs.  The later residual remains eligible on
    # all of its real dates; no calendar partition is reintroduced here.
    ordered_targets = tuple(sorted(targets, key=lambda item: (item.horizon_days, item.id)))
    for target in ordered_targets:
        reconciled = selected_by_id[target.id]
        already_credited = credited_to_later_targets.get(target.id, 0)
        residual = max(
            max(0, reconciled.remaining_rowing_minutes - already_credited),
            max(0, reconciled.remaining_low_intensity_minutes - already_credited),
        )
        if residual < minimum:
            continue
        count = max(1, (residual + preferred - 1) // preferred)
        minutes, remainder = divmod(residual, count)
        for index in range(1, count + 1):
            credit_minutes = minutes + int(index <= remainder)
            generated.append(_need(
                policy, TrainingRole.DEDICATED_UT2,
                PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, credit_minutes, True),
                (target.horizon_start, target.horizon_end), recovery_cost="low",
                priority="aerobic_target_topup",
                policy_keys=("low_intensity_minimum_fraction", "residual_aerobic_session_minutes", "load_target_range_fractions"),
                progression_relation=f"load_target_residual:{target.id}",
                provenance=("training_load_target", target.id),
            ))
        # Every earlier-horizon residual is a real candidate inside each
        # longer horizon that contains its full window.  Record its target
        # credit once for that later horizon so the next target does not
        # recreate the same physiological session.
        for later in ordered_targets:
            if later.horizon_days <= target.horizon_days:
                continue
            if later.horizon_start <= target.horizon_start and target.horizon_end <= later.horizon_end:
                credited_to_later_targets[later.id] = credited_to_later_targets.get(later.id, 0) + residual
    return expand_repeated_exposure_needs(tuple(generated))


def _active_recovery_roles(state: PhysiologicalState, planning_date: date) -> set[TrainingRole]:
    blocked: set[TrainingRole] = set()
    for constraint in state.recovery_constraints:
        if constraint.start_date <= planning_date <= constraint.end_date:
            blocked.update(constraint.hard_excluded_roles)
    return blocked


def _race_recovery_active(state: PhysiologicalState, planning_date: date) -> bool:
    return any(
        constraint.source.startswith("race:")
        and constraint.start_date <= planning_date <= constraint.end_date
        for constraint in state.recovery_constraints
    )


def _is_taper(race: Mapping[str, Any], rules: Mapping[str, TrainingPolicyRule]) -> bool:
    days = race.get("days_until")
    if not isinstance(days, int) or days < 0:
        return False
    priority = str(race.get("priority") or "B")
    return days <= _integer(rules["taper_days_by_priority"].value.get(priority, rules["taper_days_by_priority"].value["B"]))


def generate_training_need_plan(
    context: NormalizedPlanningContext,
    state: PhysiologicalState | None = None,
    policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
    *,
    profile: Mapping[str, Any] | None = None,
) -> TrainingNeedGeneration:
    """Build deterministic undated needs from normalized facts and state only."""
    state = state or reduce_physiological_state(context, profile=profile)
    if state.as_of_date != context.planning_date:
        raise ValueError("physiological_state_planning_date_mismatch")
    rules = _rules(policy)
    required = {
        "ordinary_need_horizon_days", "dedicated_ut2_target_credit_minutes",
        "long_aerobic_target_credit_minutes", "quality_target_credit_minutes",
        "rolling_strength_target_14d", "taper_days_by_priority", "taper_volume_multiplier",
    }
    missing = required - rules.keys()
    if missing:
        raise ValueError(f"missing_training_need_policy:{','.join(sorted(missing))}")

    race = _race_context(context, state)
    phase = _phase(state, race)
    taper = _is_taper(race, rules)
    recovery_blocked = _active_recovery_roles(state, context.planning_date)
    race_recovery = _race_recovery_active(state, context.planning_date)
    horizon = _integer(rules["ordinary_need_horizon_days"].value)
    ordinary_window = _window(context.planning_date, horizon, race)
    needs: list[TrainingNeed] = []
    blocked: list[str] = []
    adjustments: list[str] = []

    if race_recovery:
        recovery_end = max(
            (constraint.end_date for constraint in state.recovery_constraints if constraint.start_date <= context.planning_date <= constraint.end_date),
            default=context.planning_date,
        )
        needs.append(_need(
            policy, TrainingRole.RECOVERY_AEROBIC,
            PhysiologicalCredit(TrainingRole.RECOVERY_AEROBIC, 1, 20, True),
            (context.planning_date, min(ordinary_window[1], recovery_end)),
            recovery_cost="low", priority="recovery", policy_keys=("race_hard_recovery_exclusions",),
            race_relevance="post_race_recovery", progression_relation="reentry",
            provenance=("physiological_state", "recovery_constraint"),
        ))
        blocked.extend(sorted(item.value for item in recovery_blocked & ({TrainingRole.QUALITY, TrainingRole.STRENGTH} | _HARD_RECOVERY_ROLES)))

    taper_multiplier = float(rules["taper_volume_multiplier"].value) if taper else 1.0
    aerobic_target = round(_capacity_credit(profile, _integer(rules["dedicated_ut2_target_credit_minutes"].value)) * taper_multiplier)
    long_target = round(_capacity_credit(profile, _integer(rules["long_aerobic_target_credit_minutes"].value)) * taper_multiplier)
    completed_ut2 = state.completed_aerobic_by_classification.get("UT2", PhysiologicalCredit()).minutes
    dedicated_gap = max(0, aerobic_target - completed_ut2)
    long_allowed = not race_recovery and not taper and phase not in {"post_race_recovery", "race"}
    long_credit = min(long_target, dedicated_gap) if long_allowed else 0
    if long_allowed and long_credit:
        needs.append(_need(
            policy, TrainingRole.LONG_AEROBIC,
            PhysiologicalCredit(TrainingRole.LONG_AEROBIC, 1, long_credit, True), ordinary_window,
            recovery_cost="moderate", priority="aerobic_base",
            policy_keys=("long_aerobic_target_credit_minutes", "low_intensity_emphasis"),
            progression_relation="may_contribute:dedicated_ut2",
        ))
    if dedicated_gap - long_credit > 0 and not race_recovery:
        needs.append(_need(
            policy, TrainingRole.DEDICATED_UT2,
            PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, dedicated_gap - long_credit, True), ordinary_window,
            recovery_cost="low", priority="protected_aerobic_base",
            policy_keys=("dedicated_ut2_target_credit_minutes", "low_intensity_emphasis", "mode_specific_accounting"),
            progression_relation="aerobic_development",
        ))
    if taper:
        adjustments.append("taper_volume_reduced")
        if TrainingRole.QUALITY not in recovery_blocked:
            needs.append(_need(
                policy, TrainingRole.TAPER_MAINTENANCE,
                PhysiologicalCredit(TrainingRole.TAPER_MAINTENANCE, 1, max(1, round(_integer(rules["quality_target_credit_minutes"].value) * taper_multiplier)), True),
                ordinary_window, recovery_cost="low", priority="race_readiness",
                policy_keys=("taper_reduce_volume_retain_useful_intensity_frequency", "taper_volume_multiplier"),
                race_relevance="taper", progression_relation="TR_maintenance", state="reduced", state_reason="taper_volume_reduction",
            ))
    elif phase == "threshold_development" and TrainingRole.QUALITY not in recovery_blocked:
        needs.append(_need(
            policy, TrainingRole.THRESHOLD_AT,
            PhysiologicalCredit(TrainingRole.THRESHOLD_AT, 1, _integer(rules["quality_target_credit_minutes"].value), True), ordinary_window,
            recovery_cost="hard", priority="quality_development",
            policy_keys=("quality_categories_are_bounded_not_protocols", "quality_target_credit_minutes"),
            progression_relation="threshold_development",
        ))
    elif phase == "race_specific_preparation" and TrainingRole.QUALITY not in recovery_blocked:
        needs.append(_need(
            policy, TrainingRole.RACE_SPECIFIC_TR,
            PhysiologicalCredit(TrainingRole.RACE_SPECIFIC_TR, 1, _integer(rules["quality_target_credit_minutes"].value), True), ordinary_window,
            recovery_cost="hard", priority="race_specific",
            policy_keys=("ordinary_race_specific_work_is_tr_unless_explicitly_anaerobic_or_power", "quality_target_credit_minutes"),
            race_relevance="upcoming_race", progression_relation="race_specific_development",
        ))
    elif race and isinstance(race.get("days_until"), int) and 1 < race["days_until"] <= 7 and TrainingRole.QUALITY not in recovery_blocked:
        needs.append(_need(
            policy, TrainingRole.RACE_PREPARATION,
            PhysiologicalCredit(TrainingRole.RACE_PREPARATION, 1, _integer(rules["quality_target_credit_minutes"].value), True), ordinary_window,
            recovery_cost="moderate", priority="race_readiness",
            policy_keys=("ordinary_race_specific_work_is_tr_unless_explicitly_anaerobic_or_power",),
            race_relevance="upcoming_race", progression_relation="TR_rhythm",
        ))

    strength = rules["rolling_strength_target_14d"].value
    taper_strength = rules["taper_strength_continuity"].value
    required_strength = _integer(taper_strength["target"]) if taper else _integer(strength["minimum"])
    strength_gap = max(0, required_strength - state.completed_strength_exposure.exposures)
    if strength_gap and TrainingRole.STRENGTH not in recovery_blocked:
        needs.append(_need(
            policy, TrainingRole.STRENGTH,
            PhysiologicalCredit(TrainingRole.STRENGTH, strength_gap, 0, True), ordinary_window,
            recovery_cost="hard", priority="strength_taper_maintenance" if taper else "required_strength_minimum",
            policy_keys=(("taper_strength_continuity", "concurrent_strength_endurance_is_individualized") if taper else ("rolling_strength_target_14d", "concurrent_strength_endurance_is_individualized")),
            progression_relation="taper_strength_continuity" if taper else "rolling_14d_strength",
            state="reduced" if taper else "active", state_reason="taper_strength_continuity" if taper else None,
        ))
    elif strength_gap and TrainingRole.STRENGTH in recovery_blocked:
        blocked.append(TrainingRole.STRENGTH.value)

    ordered = expand_repeated_exposure_needs(needs)
    active_counts: dict[str, int] = {}
    for need in ordered:
        if need.state in {"active", "reduced"}:
            active_counts[need.role.value] = active_counts.get(need.role.value, 0) + 1
    repeated_by_role: dict[str, int] = {}
    for need in ordered:
        if need.exposure_count > 1:
            repeated_by_role[need.role.value] = repeated_by_role.get(need.role.value, 0) + 1
    diagnostics = TrainingNeedDiagnostics(
        active_need_count_by_role=active_counts,
        needs_deferred=tuple(item.id for item in ordered if item.state == "deferred"),
        needs_reduced=tuple(item.id for item in ordered if item.state == "reduced"),
        needs_superseded=tuple(item.id for item in ordered if item.state == "superseded"),
        needs_blocked_by_recovery=tuple(sorted(set(blocked))),
        race_taper_adjustments=tuple(sorted(adjustments)),
        source_policy_ids=tuple(sorted({key for item in ordered for key in item.source_policy.split("|")})),
        repeated_needs_generated_by_role=repeated_by_role,
    )
    return TrainingNeedGeneration(ordered, diagnostics)


def generate_training_needs(
    context: NormalizedPlanningContext,
    state: PhysiologicalState | None = None,
    policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
    *,
    profile: Mapping[str, Any] | None = None,
) -> tuple[TrainingNeed, ...]:
    """Return ordered needs; use ``generate_training_need_plan`` for diagnostics."""
    return generate_training_need_plan(context, state, policy, profile=profile).needs
