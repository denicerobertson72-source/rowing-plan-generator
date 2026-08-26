"""Versioned API contracts. The FastAPI OpenAPI document is authoritative."""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

class PlanGenerationRequest(BaseModel):
    api_version: Literal["v1"] = "v1"
    athlete_profile: dict[str, Any]
    locked_sessions: list[dict[str, Any]] = Field(default_factory=list)

class PlanResponse(BaseModel):
    plan_id: str
    plan: dict[str, Any]

class AthleteCreateRequest(BaseModel):
    athlete_profile: dict[str, Any]
    user_id: Optional[str] = None

class AthleteResponse(BaseModel):
    athlete_id: str
    athlete_profile: dict[str, Any]

class RegenerateRequest(BaseModel):
    locked_sessions: list[dict[str, Any]] = Field(default_factory=list)

class WorkoutLogRequest(BaseModel):
    status: Literal["completed", "modified", "skipped"]
    actual_duration_min: Optional[int] = Field(default=None, ge=0)
    rpe: Optional[int] = Field(default=None, ge=1, le=10)
    average_hr: Optional[int] = Field(default=None, ge=0)
    peak_hr: Optional[int] = Field(default=None, ge=0)
    average_watts: Optional[float] = Field(default=None, ge=0)
    average_split_seconds: Optional[float] = Field(default=None, ge=0)
    average_rate: Optional[int] = Field(default=None, ge=0)
    coach_changed_session: bool = False
    technical_note: str = ""
    conditions: str = ""
    notes: str = ""

class PrivateCheckInRequest(BaseModel):
    entry_date: str
    female_health_tracking_enabled: bool = False
    bleeding_today: Optional[bool] = None
    symptom_impact: Optional[Literal["none","mild","moderate","high"]] = None
    symptom_tags: list[str] = Field(default_factory=list)
    life_stage_tracking_enabled: bool = False
    life_stage: Optional[Literal["premenopausal","perimenopausal","postmenopausal","unsure","prefer_not_to_say"]] = None
    sleep_impact: Optional[Literal["none","mild","moderate","high"]] = None
    fatigue_impact: Optional[Literal["none","mild","moderate","high"]] = None
    muscle_joint_impact: Optional[Literal["none","mild","moderate","high"]] = None
    heat_symptom_impact: Optional[Literal["none","mild","moderate","high"]] = None
    hrv_ms: Optional[float] = Field(default=None, ge=0)
    athlete_note: str = ""

class WeeklyOverrideRequest(BaseModel):
    week_start: str
    scope: Literal["this_week_only","make_normal_schedule"] = "this_week_only"
    changes: list[dict[str, Any]] = Field(default_factory=list)

class ApiHealth(BaseModel):
    status: Literal["ok"]
    api_version: str
    planner_version: str
