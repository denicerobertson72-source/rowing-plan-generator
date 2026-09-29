"""Small typed model helpers; public plans deliberately remain JSON dictionaries."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Literal, Mapping
from datetime import date

@dataclass(frozen=True)
class Band:
    name: str
    domain: str
    hr_low: int | None = None
    hr_high: int | None = None
    watts_low: float | None = None
    watts_high: float | None = None
    spm_low: int | None = None
    spm_high: int | None = None
    effort_low: float | None = None
    effort_high: float | None = None
    method: str = "hrr_fallback"
    confidence: str = "low"
    assumptions: list[str] | None = None
    def to_dict(self) -> dict[str, Any]: return asdict(self)

@dataclass(frozen=True)
class SeasonPhase:
    """A persisted, explainable season-level planning decision."""
    phase_id: str
    phase_type: str
    start_date: str
    end_date: str
    primary_objectives: list[str]
    secondary_objectives: list[str]
    priority_bands: list[str]
    maintain_bands: list[str]
    volume_direction: str
    specificity_level: int
    race_rate_exposure: str
    strength_emphasis: str
    target_race_id: str | None
    source_ids: list[str]
    reason: str
    algorithm_version: str
    def to_dict(self) -> dict[str, Any]: return asdict(self)

@dataclass(frozen=True)
class WeeklyTrainingIntent:
    """A persisted weekly objective, intentionally independent of templates."""
    week_start: str
    phase_id: str
    target_rowing_sessions: int
    target_total_rowing_exposures: int
    target_coached_rowing_exposures: int
    target_independent_rowing_exposures: int
    target_strength_sessions: int
    target_rest_days: int
    target_private_coaching_sessions: int
    target_coached_row_sessions: int
    primary_session_roles: list[str]
    secondary_session_roles: list[str]
    target_low_intensity_minutes: int
    target_moderate_minutes: int
    target_high_intensity_minutes: int
    target_total_rowing_minutes: int
    race_specific_minutes: int
    load_direction: str
    testing_or_race_events: list[dict[str, Any]]
    taper_volume_factor: float
    volume_target_factor: float
    phase_mix: list[dict[str, Any]]
    transition_note: str | None
    next_race_name: str | None
    next_race_priority: str | None
    notes: str
    algorithm_version: str
    def to_dict(self) -> dict[str, Any]: return asdict(self)

@dataclass(frozen=True)
class TrainingDemand:
    """Undated V2 training need; internal only, never an API schema."""
    demand_id: str; type: str; phase_id: str; earliest_date: date; latest_date: date
    desired_dates: tuple[date,...]=(); preferred_dates: tuple[date,...]=()
    priority: Literal["hard","strong","soft"]="soft"; target_minutes: int|None=None
    quality_class: Literal["none","aerobic","quality","race"]="none"
    minimum_recovery_days: int=0; frequency_group: str|None=None; source: str=""; rationale: str=""
    canonical_week_start: date|None=None; canonical_week_end: date|None=None; eligibility: Literal["required","edge_exception"]="required"
    def to_dict(self) -> dict[str,Any]:
        result=asdict(self)
        for key in ("earliest_date","latest_date"): result[key]=result[key].isoformat()
        for key in ("desired_dates","preferred_dates"): result[key]=[value.isoformat() for value in result[key]]
        return result

@dataclass(frozen=True)
class RollingState:
    last_quality_date: date|None=None; last_strength_date: date|None=None
    quality_minutes_14d: int=0; strength_count_14d: int=0; dedicated_ut2_minutes_14d: int=0; ut1_minutes_14d: int=0; mixed_coached_minutes_14d: int=0
    long_row_dates: tuple[date,...]=(); race_load_dates: tuple[date,...]=(); completed_actuals: tuple[dict[str,Any],...]=()

@dataclass(frozen=True)
class PlacementCandidate:
    demand_id: str; date: date; score_components: tuple[tuple[str,int],...]=(); hard_failures: tuple[str,...]=(); reason_codes: tuple[str,...]=()

@dataclass(frozen=True)
class FrequencyTarget:
    """A V2 rolling-group target, deliberately separate from dated demands."""
    group_id: str; window_days: int; target_count: int; minimum_count: int
    maximum_count: int|None=None; priority: Literal["hard","strong","soft"]="strong"
    minimum_spacing_days: int=0; source: str=""; rationale: str=""
    def to_dict(self) -> dict[str,Any]: return asdict(self)

@dataclass(frozen=True)
class TrainingDoseTarget:
    phase_id: str; category: str; window_start: date; window_end: date; window_days: int
    target_exposures: int; minimum_exposures: int; target_minutes: int; minimum_minutes: int
    quality_class: Literal["none","aerobic","quality","race"]="none"; priority: Literal["hard","strong","soft"]="strong"
    minimum_recovery_days: int=0; source: str="season_phase"; rationale: str=""
    def to_dict(self) -> dict[str,Any]:
        result=asdict(self); result["window_start"]=self.window_start.isoformat(); result["window_end"]=self.window_end.isoformat(); return result

@dataclass(frozen=True)
class V2DemandPlan:
    weekly_commitment_demands: tuple[TrainingDemand,...]
    frequency_targets: tuple[FrequencyTarget,...]
    rowing_dose_targets: tuple[TrainingDoseTarget,...]

@dataclass(frozen=True)
class DateContext:
    date: date; weekday: str; phase_id: str; available: bool; max_training_minutes: int
    hard_committed_minutes: int; remaining_minutes: int; unavailable: bool=False; race: bool=False; race_practice: bool=False
    taper_or_recovery: bool=False; fixed_commitments: tuple[dict[str,Any],...]=(); completed_sessions: tuple[dict[str,Any],...]=()
    permitted_activity_categories: tuple[str,...]=(); diagnostic_reason_codes: tuple[str,...]=()
    prohibited_role_families: tuple[str,...]=()

@dataclass(frozen=True)
class CandidateDateResult:
    candidates: tuple[date,...]; rejections: tuple[tuple[str,tuple[str,...]],...]=()

@dataclass(frozen=True)
class RollingPlacementResult:
    placements: tuple[tuple[str,date],...]; audits: tuple[dict[str,Any],...]; strong_target_misses: tuple[dict[str,Any],...]
    beam_width: int; states_explored: int; states_retained: int

@dataclass(frozen=True)
class TargetCredit:
    target_id: str; category: str; exposures: int; minutes: int

@dataclass(frozen=True)
class WindowPlacement:
    placement_id: str; date: date; role: str; source_id: str; minutes: int; frozen: bool=False
    credits: tuple[TargetCredit,...]=()
    user_fixed: bool=False; original_date: date|None=None; override_id: str|None=None

@dataclass(frozen=True)
class UserScheduleOverride:
    override_id: str; action_type: Literal["move","swap","day_restriction"]
    placement_ids: tuple[str,...]=(); from_dates: tuple[date,...]=(); to_dates: tuple[date,...]=(); reason: str|None=None

@dataclass(frozen=True)
class ScheduleChangeResult:
    success: bool; hard_failures: tuple[str,...]=(); target_consequences: tuple[dict[str,Any],...]=()
    state: Any=None; overrides: tuple[UserScheduleOverride,...]=()

@dataclass(frozen=True)
class RepairScope:
    """Pure description of the bounded calendar affected by a user change."""
    changed_dates: tuple[date,...]
    mutable_start: date
    mutable_end: date
    history_start: date
    reconciliation_end: date

@dataclass(frozen=True)
class ReopenedPlacement:
    """Identity and prior facts retained while planner-owned work is reopened."""
    placement_id: str
    source_id: str
    prior_date: date
    prior_role: str
    prior_minutes: int
    prior_credits: tuple[TargetCredit,...]=()

@dataclass(frozen=True)
class RepairReconstructionResult:
    """Repair-ready state only; it intentionally contains no repaired output."""
    scope: RepairScope
    state: Any
    immutable_history: tuple[WindowPlacement,...]
    preserved_user_fixed: tuple[WindowPlacement,...]
    reopened_placements: tuple[ReopenedPlacement,...]
    reopened_source_ids: tuple[str,...]
    untouched_placements: tuple[WindowPlacement,...]=()

@dataclass(frozen=True)
class LocalRepairResult:
    """Pure local-repair transaction result; consequence reporting comes later."""
    success: bool
    state: Any
    merged_placements: tuple[WindowPlacement,...]=()
    scope: RepairScope|None=None
    reconstruction: RepairReconstructionResult|None=None
    hard_failures: tuple[str,...]=()
    overrides: tuple[UserScheduleOverride,...]=()
    repair_changes: tuple[Any,...]=()
    target_consequences: tuple[Any,...]=()

@dataclass(frozen=True)
class RepairChange:
    kind: Literal["moved","removed","added","role_changed"]
    placement_id: str; source_id: str; from_date: date|None=None; to_date: date|None=None; role: str|None=None

@dataclass(frozen=True)
class TargetConsequence:
    target_id: str; category: str; before_status: str; after_status: str
    before_value: int; after_value: int; minimum: int; direction: Literal["worsened","improved"]

@dataclass(frozen=True)
class DatedTrainingRole:
    """Generic downstream constraint, never a concrete workout prescription."""
    date: date; role: str; duration_minutes: int; phase_id: str; source_id: str
    provenance: Literal["fixed","frozen","provisional"]; target_credits: tuple[TargetCredit,...]; placement_id: str

@dataclass(frozen=True)
class ActiveWindowState:
    window_start: date; window_end: date
    frozen_placements: tuple[WindowPlacement,...]; provisional_placements: tuple[WindowPlacement,...]
    remaining_minutes_by_date: Mapping[date,int]; fixed_context: Mapping[date,DateContext]
    weekly_commitment_status: Mapping[str,Any]; frequency_credits: Mapping[str,tuple[int,int]]; rowing_dose_credits: Mapping[str,tuple[int,int]]
    last_frozen_quality_date: date|None=None; last_frozen_strength_date: date|None=None
    score_vector: tuple[int,...]=(); audits: tuple[dict[str,Any],...]=(); exceptions: tuple[dict[str,Any],...]=()
    demand_satisfaction: Mapping[str,Any]=None

@dataclass(frozen=True)
class DemandSatisfaction:
    demand_id: str; canonical_week_start: date; canonical_week_end: date
    eligibility: Literal["required","edge_exception"]; status: Literal["open","provisional_satisfied","frozen_satisfied","missed","edge_exception"]
    placement_id: str|None=None; placement_date: date|None=None; provenance: str="derived"
