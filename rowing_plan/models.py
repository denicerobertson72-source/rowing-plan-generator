"""Small typed model helpers; public plans deliberately remain JSON dictionaries."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

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
