"""API-first adapter around the preserved deterministic planning engine."""
from __future__ import annotations
import json
import logging
import os
import sys
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from traceback import extract_tb
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from rowing_plan import PLANNER_VERSION
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import PlanningConflict, generate_plan
from rowing_plan.validators import hard_constraint_errors, validate_profile
from rowing_plan.workbook import build_workbook
from .repositories import REPOSITORIES, profile_revision, public_profile, with_profile_revision
from .auth import current_user_id
from rowing_plan.training_load import load_summary, session_load_au
from rowing_plan.recurring_activities import normalize_recurring_schedule_for_planning, schedule_signature
from .schemas import ApiHealth, AthleteCreateRequest, AthleteResponse, AthleteUpdateRequest, PlanGenerationRequest, PlanResponse, PrivateCheckInRequest, RacePostingRequest, RegenerateRequest, WeeklyOverrideRequest, WorkoutLogRequest

CONFIG = json.loads((ROOT / "config/defaults.json").read_text())
logger = logging.getLogger(__name__)
app = FastAPI(title="Rowing Plan API", version="0.4.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
allowed_origins=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def prevent_dynamic_api_caching(request, call_next):
    response=await call_next(request)
    if request.url.path.startswith("/api/v1/") and request.method=="GET": response.headers["Cache-Control"]="no-store"
    return response

def build_plan(request: PlanGenerationRequest) -> dict:
    profile = normalize_recurring_schedule_for_planning(request.athlete_profile)
    errors = validate_profile(profile)
    if errors: raise HTTPException(status_code=422, detail={"error_code":"profile_validation","validation_errors": errors})
    bands = build_intensity_profile(profile, CONFIG)
    power = build_power_profile(profile, CONFIG)
    try: plan = generate_plan(profile, CONFIG, bands, power, request.locked_sessions)
    except PlanningConflict as error: raise HTTPException(status_code=422, detail={"error_code":"planning_conflict","planning_conflicts":[str(error)],"diagnostic":error.details}) from error
    hard_errors = hard_constraint_errors(plan, profile)
    if hard_errors: raise HTTPException(status_code=422, detail={"error_code":"hard_constraint","constraint_errors": hard_errors})
    return plan

def owned_athlete(athlete_id: str, user_id: str) -> dict:
    profile=REPOSITORIES.get(athlete_id)
    if not profile: raise HTTPException(404,"Athlete not found")
    if REPOSITORIES.athlete_owner(athlete_id) != user_id: raise HTTPException(403,"This athlete belongs to another account.")
    return profile

def safe_generation_metadata(profile: dict) -> dict:
    """Log only structural planner context for an unexpected generation failure."""
    activities=profile.get("recurring_activities")
    tests=profile.get("tests") if isinstance(profile.get("tests"),dict) else {}
    return {
        "recurring_activities_present":activities is not None,
        "recurring_activity_count":len(activities) if isinstance(activities,list) else 0,
        "race_count":len(profile.get("races",[])) if isinstance(profile.get("races"),list) else 0,
        "performance_test_count":len(tests.get("multi_duration_power_tests",[])) if isinstance(tests.get("multi_duration_power_tests"),list) else 0,
        "season_dates_present":bool(profile.get("season",{}).get("start_date") and profile.get("season",{}).get("end_date")),
    }

def unexpected_generation_error(error: Exception, profile: dict, athlete_id: str | None, stage: str, planversion_creation_started: bool) -> HTTPException:
    error_id=f"pg_{token_urlsafe(6).replace('-', '').replace('_', '')}"
    trace=extract_tb(error.__traceback__)
    source=f"{trace[-1].filename}:{trace[-1].lineno}" if trace else "unknown"
    metadata=safe_generation_metadata(profile)
    logger.exception(
        "plan_generation_failed error_id=%s exception_class=%s exception_message=%s source=%s stage=%s athlete_id_present=%s planversion_creation_started=%s recurring_activities_present=%s recurring_activity_count=%s race_count=%s performance_test_count=%s season_dates_present=%s",
        error_id, type(error).__name__, str(error), source, stage, bool(athlete_id), planversion_creation_started,
        metadata["recurring_activities_present"], metadata["recurring_activity_count"], metadata["race_count"], metadata["performance_test_count"], metadata["season_dates_present"],
    )
    return HTTPException(500, detail={"error_code":"plan_generation_failed","error_id":error_id})
def athlete_response(athlete_id: str, profile: dict) -> AthleteResponse:
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=public_profile(profile), profile_revision=profile_revision(profile))
def athlete_summary(record: dict) -> dict:
    profile=record["athlete_profile"]
    athlete=profile.get("athlete",{})
    season=profile.get("season",{})
    blocks=profile.get("tests",{}).get("testing_blocks",[])
    deletion_status=REPOSITORIES.deletion_status(record["athlete_id"])
    return {"athlete_id":record["athlete_id"],"created_at":record["created_at"],"updated_at":record["updated_at"],"display_name":athlete.get("display_name") or "Unnamed rower","season_name":season.get("season_name") or "","season_start":season.get("start_date"),"season_end":season.get("end_date"),"race_count":len(profile.get("races",[])),"recurring_activity_count":len(profile.get("recurring_activities",[])),"performance_test_count":sum(len(block.get("performance_tests",[])) for block in blocks if isinstance(block,dict)),"plan_id":record.get("plan_id"),"deletion_status":deletion_status,"can_delete":deletion_status in {"eligible","configured"}}
def owned_plan(plan_id: str, user_id: str) -> dict:
    record=REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404,"Plan not found")
    if REPOSITORIES.plan_owner(plan_id) != user_id: raise HTTPException(403,"This plan belongs to another account.")
    return record
def plan_needs_update(record: dict, profile: dict) -> bool:
    return record["plan"].get("schedule_signature") != schedule_signature(profile)
def stable_session_key(session: dict) -> str:
    return f'{session["date"]}:{session.get("session_id")}:{session.get("mode")}'
def coached_session(session: dict) -> bool:
    return session.get("session_id")=="COACHED" or session.get("coached") is True
def actual_by_key(plan_id: str) -> dict[str, dict]:
    actuals={entry["session_key"]:entry["payload"] for entry in REPOSITORIES.logs_for_plan(plan_id)}
    # An accepted adjustment is a new immutable PlanVersion.  Its source log
    # remains canonical on the source version, so inherit it for display
    # rather than cloning a second completion record.
    record=REPOSITORIES.get_plan(plan_id)
    for provenance in (record or {}).get("plan",{}).get("adjustment_provenance",[]):
        source=provenance.get("source_plan_id")
        if source:
            actuals={**{entry["session_key"]:entry["payload"] for entry in REPOSITORIES.logs_for_plan(source)},**actuals}
    return actuals
def session_load_classification(payload: dict) -> str:
    if payload.get("completion")=="no" or payload.get("status")=="skipped": return "missed"
    composition=actual_composition(payload)
    if composition["quality_seconds"] >= 8*60: return "unusually_hard_or_long" if composition["quality_seconds"] >= 25*60 else "quality_hard"
    intensity=payload.get("actual_intensity")
    rpe=payload.get("rpe") or 0
    minutes=payload.get("actual_duration_min") or 0
    if intensity in {"AT","TR","AN","PP"} or (intensity=="mixed_unsure" and rpe>=7): return "unusually_hard_or_long" if minutes>=75 or rpe>=9 else "quality_hard"
    if intensity in {"UT2","UT1"} or (intensity=="mixed_unsure" and rpe>=5): return "aerobic_moderate"
    return "easy_technical"
def actual_composition(payload: dict) -> dict:
    """Describe athlete-entered segments without inferring unknown portions."""
    totals={"easy_technical_seconds":0,"aerobic_seconds":0,"quality_seconds":0,"unclassified_seconds":0}
    for segment in payload.get("actual_segments",[]):
        seconds=(segment.get("duration_seconds") or 0)*max(1,segment.get("repetitions") or 1)
        band=segment.get("intensity_band")
        if band in {"AT","TR","AN","PP"}: bucket="quality_seconds"
        elif band in {"UT2","UT1"}: bucket="aerobic_seconds"
        elif band=="UT3" or segment.get("segment_type") in {"warm_up","technical_drill","easy_rowing","recovery","cooldown_return"}: bucket="easy_technical_seconds"
        else: bucket="unclassified_seconds"
        totals[bucket]+=seconds
    known=sum(totals.values())
    if payload.get("actual_duration_min") and known < payload["actual_duration_min"]*60: totals["unclassified_seconds"]+=payload["actual_duration_min"]*60-known
    return {**totals,"total_seconds":sum(totals.values())}
def impact_explanation(record: dict, source: dict, classification: str, changes: list[dict], payload: dict) -> str:
    composition=actual_composition(payload); quality=composition["quality_seconds"]//60
    after=sorted((s for s in record["plan"].get("sessions",[]) if s["date"]>source["date"]),key=lambda s:s["date"])
    next_row=next((s for s in after if s.get("mode") in {"erg","on_water"}),None)
    recovery=[s.get("title","session").lower() for s in after if next_row and s["date"]<next_row["date"]]
    detail=f"This session included about {quality} minutes of high-intensity rowing." if quality else "The logged load fits the planned coaching session."
    weekly=weekly_quality_context(record,source,payload)
    if changes and weekly["completed_exposures"]>=2: return f"Review recommended. You have already completed {weekly['completed_exposures']} quality rowing exposures this week: {', '.join(weekly['labels'])}. Sunday quality work would create a third exposure, so a lower-intensity stimulus is suggested instead."
    if changes: return f"{detail} Your next rowing session is quality work, so its spacing should be reviewed."
    if next_row: return f"{detail} The next rowing session is {next_row.get('band','planned work')}; {' and '.join(recovery) or 'the available spacing'} supports keeping the current plan."
    return f"{detail} No later rowing session this week needs changing."
def weekly_quality_context(record: dict, source: dict, source_payload: dict) -> dict:
    """Count completed quality exposures, including partial coached quality work."""
    source_date=date.fromisoformat(source["date"]); monday=source_date-timedelta(days=source_date.weekday())
    actuals=actual_by_key(record["plan_id"]); actuals[stable_session_key(source)]=source_payload
    labels=[]; minutes=0
    for session in record["plan"].get("sessions",[]):
        session_date=date.fromisoformat(session["date"])
        if not monday<=session_date<=source_date: continue
        actual=actuals.get(stable_session_key(session))
        if not actual or actual.get("completion")=="no" or actual.get("status")=="skipped": continue
        composition=actual_composition(actual)
        quality_seconds=composition["quality_seconds"]
        is_quality=quality_seconds>=8*60 or (not actual.get("actual_segments") and any(b in str(actual.get("actual_intensity") or session.get("band","")) for b in ("AT","TR","AN","PP")))
        if is_quality:
            labels.append(session.get("title") or session.get("session_role") or "quality work")
            minutes+=quality_seconds//60
    return {"completed_exposures":len(labels),"quality_minutes":minutes,"labels":labels}
def bounded_coached_proposal(record: dict, session_key: str, payload: dict) -> dict:
    source=next((s for s in record["plan"].get("sessions",[]) if stable_session_key(s)==session_key),None)
    if not source or not coached_session(source): raise HTTPException(422,"Only coached rows and private coaching sessions can use this log.")
    classification=session_load_classification(payload)
    if classification not in {"quality_hard","unusually_hard_or_long"}: return {"recommendation":"none","classification":classification,"changes":[],"composition":actual_composition(payload),"explanation":impact_explanation(record,source,classification,[],payload)}
    source_date=date.fromisoformat(source["date"]); monday=source_date-timedelta(days=source_date.weekday())
    candidates=[s for s in record["plan"].get("sessions",[]) if monday < date.fromisoformat(s["date"]) < monday+timedelta(days=7) and date.fromisoformat(s["date"])>source_date and any(b in str(s.get("band","")) for b in ("AT","TR","AN","PP"))]
    if not candidates: return {"recommendation":"none","classification":classification,"changes":[],"composition":actual_composition(payload),"explanation":impact_explanation(record,source,classification,[],payload)}
    current=candidates[0]; suggested={**current,"band":"UT2","title":"Long aerobic","structure":"60 min UT2","description":"Adjusted after an unexpectedly hard coached session.","adjustment_reason":"coached_session_actual"}
    changes=[{"session_key":stable_session_key(current),"date":current["date"],"current":current,"suggested":suggested,"why":"Coaching added an unexpected hard rowing exposure; this preserves recovery spacing."}]
    return {"recommendation":"review","classification":classification,"source_session_id":session_key,"changes":changes,"composition":actual_composition(payload),"explanation":impact_explanation(record,source,classification,changes,payload)}
def enrich_sessions(plan_id: str, sessions: list[dict]) -> list[dict]:
    actuals=actual_by_key(plan_id); enriched=[]; cues=[]
    for session in sessions:
        item=deepcopy(session); actual=actuals.get(stable_session_key(session))
        if actual: item["actual"]=actual
        if actual and actual.get("carry_cue_forward") and (actual.get("coach_cues") or actual.get("technical_note")):
            cues.append(actual.get("coach_cues") or actual.get("technical_note"))
        elif cues and session.get("mode") in {"on_water","erg"}: item["technical_cues"]=cues[-1:]
        enriched.append(item)
    return enriched
def require_coach_admin(user_id: str = Depends(current_user_id)) -> str:
    allowed={value.strip() for value in os.getenv("COACH_ADMIN_USER_IDS","").split(",") if value.strip()}
    if user_id not in allowed: raise HTTPException(403,"Coach/admin access is required for race postings.")
    return user_id

@app.get("/api/v1/health", response_model=ApiHealth)
def health() -> ApiHealth:
    return ApiHealth(status="ok", api_version="v1", planner_version=PLANNER_VERSION, build_id=os.getenv("VERCEL_GIT_COMMIT_SHA", "local")[:12])

@app.get("/api/v1/ready")
def ready() -> dict:
    if os.getenv("VERCEL") != "1": return {"status":"ok","environment":"local"}
    required=["SUPABASE_DB_URL","SUPABASE_URL","SUPABASE_PUBLISHABLE_KEY","ALLOWED_ORIGINS"]
    missing=[name for name in required if not os.getenv(name)]
    if os.getenv("REQUIRE_AUTH", "false").lower() != "true": missing.append("REQUIRE_AUTH=true")
    if missing: raise HTTPException(503, {"status":"not_ready","missing":missing})
    return {"status":"ok","environment":"vercel"}

@app.post("/api/v1/plans/generate", response_model=PlanResponse)
def generate(request: PlanGenerationRequest) -> PlanResponse:
    plan = build_plan(request)
    athlete_id=REPOSITORIES.create(request.athlete_profile)
    return PlanResponse(plan_id=REPOSITORIES.save_plan(athlete_id, plan), plan=plan)

@app.post("/api/v1/athletes", response_model=AthleteResponse)
def create_athlete(request: AthleteCreateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    profile=with_profile_revision(request.athlete_profile, 0)
    athlete_id=REPOSITORIES.create(profile, user_id)
    return athlete_response(athlete_id, profile)

@app.post("/api/v1/account/onboarding-athlete", response_model=AthleteResponse)
def get_or_create_onboarding_athlete(request: AthleteCreateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    """The only creation path used by first-run onboarding; safe under retries."""
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    profile=with_profile_revision(request.athlete_profile, 0)
    athlete_id, stored_profile, _=REPOSITORIES.get_or_create_for_user(profile, user_id)
    return athlete_response(athlete_id, stored_profile)

@app.get("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def get_athlete(athlete_id: str, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    profile=owned_athlete(athlete_id,user_id)
    return athlete_response(athlete_id, profile)

@app.get("/api/v1/account/athlete")
def get_current_athlete(user_id: str = Depends(current_user_id)) -> dict:
    record=REPOSITORIES.latest_for_user(user_id)
    if not record: raise HTTPException(404,"No Athlete Profile is associated with this account.")
    return {"athlete_id":record["athlete_id"],"athlete_profile":public_profile(record["athlete_profile"]),"profile_revision":profile_revision(record["athlete_profile"]),"plan_id":record.get("plan_id")}

@app.get("/api/v1/account/athletes")
def get_account_athletes(user_id: str = Depends(current_user_id)) -> dict:
    return {"athletes":[athlete_summary(record) for record in REPOSITORIES.list_for_user(user_id)]}

@app.delete("/api/v1/account/athletes/{athlete_id}")
def delete_account_athlete(athlete_id: str, selected_athlete_id: Optional[str] = None, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id, user_id)
    if selected_athlete_id == athlete_id:
        raise HTTPException(409, "Select another athlete profile before deleting this one.")
    status=REPOSITORIES.delete_empty_athlete(athlete_id)
    if status == "deleted": return {"status":"deleted","athlete_id":athlete_id}
    if status == "not_found": raise HTTPException(404,"Athlete not found")
    reasons={"plan_versions":"Profiles with generated plans cannot be deleted.","athlete_records":"Profiles with saved training history cannot be deleted."}
    raise HTTPException(409, reasons.get(status,"This athlete profile cannot be deleted."))

@app.put("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def update_athlete(athlete_id: str, request: AthleteUpdateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    current=owned_athlete(athlete_id,user_id)
    updated_profile=normalize_recurring_schedule_for_planning(request.athlete_profile)
    errors=validate_profile(updated_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    current_revision=profile_revision(current)
    if request.expected_revision != current_revision: raise HTTPException(status_code=409, detail={"message":"This profile was updated in another tab or session.","current_revision":current_revision})
    profile=with_profile_revision(updated_profile, current_revision+1)
    if not REPOSITORIES.save_if_revision(athlete_id, profile, current_revision):
        latest=REPOSITORIES.get(athlete_id) or current
        raise HTTPException(status_code=409, detail={"message":"This profile was updated in another tab or session.","current_revision":profile_revision(latest)})
    return athlete_response(athlete_id, profile)

@app.post("/api/v1/athletes/{athlete_id}/plans/generate", response_model=PlanResponse)
def generate_for_athlete(athlete_id: str, request: RegenerateRequest, user_id: str = Depends(current_user_id)) -> PlanResponse:
    profile=owned_athlete(athlete_id,user_id)
    stage="locked_session_loading"; planversion_creation_started=False
    try:
        previous=REPOSITORIES.latest_plan_for_athlete(athlete_id)
        locked=list(request.locked_sessions)
        if previous:
            completed={entry["session_key"] for entry in REPOSITORIES.logs_for_plan(previous["plan_id"]) if entry["payload"].get("status")=="completed"}
            locked.extend(session for session in previous["plan"].get("sessions",[]) if f'{session["date"]}:{session.get("session_id")}:{session.get("mode")}' in completed)
        stage="plan_generation"
        plan=build_plan(PlanGenerationRequest(athlete_profile=profile, locked_sessions=locked))
    except HTTPException as error:
        detail=error.detail if isinstance(error.detail, dict) else {}
        code=detail.get("error_code", "planning_rejected" if error.status_code == 422 else "request_rejected")
        diagnostic=detail.get("diagnostic") if isinstance(detail.get("diagnostic"), dict) else {}
        logger.warning("plan_generation_failed endpoint=athlete_regenerate status=%s code=%s conflict_type=%s reason=%s activity_type=%s scheduling_status=%s requested_frequency=%s candidate_days=%s prohibited_days=%s fixed_days=%s week_start=%s validation_rule=%s", error.status_code, code, diagnostic.get("conflict_type","request_rejected"), diagnostic.get("reason","request_rejected"), diagnostic.get("activity_type"), diagnostic.get("scheduling_status"), diagnostic.get("requested_frequency"), diagnostic.get("candidate_days"), diagnostic.get("prohibited_days"), diagnostic.get("fixed_days"), diagnostic.get("week_start"), diagnostic.get("validation_rule"))
        raise
    except Exception as error:
        raise unexpected_generation_error(error, profile, athlete_id, stage, planversion_creation_started) from error
    try:
        stage="persistence"; planversion_creation_started=True
        plan_id=REPOSITORIES.save_plan(athlete_id,plan)
    except Exception as error:
        raise unexpected_generation_error(error, profile, athlete_id, stage, planversion_creation_started) from error
    return PlanResponse(plan_id=plan_id, plan=plan)

@app.get("/api/v1/athletes/{athlete_id}/plans/latest")
def latest_plan_for_athlete(athlete_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    record=REPOSITORIES.latest_plan_for_athlete(athlete_id)
    if not record: raise HTTPException(404,"No PlanVersion exists for this athlete.")
    profile=REPOSITORIES.get(athlete_id) or {}
    return {**record,"plan_needs_update":plan_needs_update(record,profile)}

@app.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id)
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    return {**record,"plan_needs_update":plan_needs_update(record,profile)}

@app.get("/api/v1/plans/{plan_id}/today")
def today(plan_id: str, on: Optional[date] = None, user_id: str = Depends(current_user_id)) -> dict:
    record = owned_plan(plan_id,user_id)
    target = (on or date.today()).isoformat()
    sessions = [s for s in record["plan"]["sessions"] if s["date"] == target]
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    return {"plan_id": plan_id, "plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile),"date": target, "sessions": enrich_sessions(plan_id,sessions), "cached_at": date.today().isoformat()}

@app.get("/api/v1/plans/{plan_id}/week")
def week(plan_id: str, week_number: Optional[int] = None, week_start: Optional[date] = None, user_id: str = Depends(current_user_id)) -> dict:
    record = owned_plan(plan_id,user_id)
    sessions = record["plan"]["sessions"]
    if week_start:
        monday=week_start-timedelta(days=week_start.weekday())
        sessions=[s for s in sessions if monday <= date.fromisoformat(s["date"]) < monday+timedelta(days=7)]
    else:
        if week_number is None: raise HTTPException(422,"Provide week_start or week_number")
        sessions=[s for s in sessions if date.fromisoformat(s["date"]).isocalendar().week == week_number]
        if not sessions: return {"plan_id": plan_id, "week": week_number, "days": []}
        first=min(date.fromisoformat(s["date"]) for s in sessions)
        monday=first-timedelta(days=first.weekday())
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    calendar={item["date"]:item for item in record["plan"].get("calendar_days",[])}
    # The newest saved override for this week takes precedence.  It is applied
    # only to this read model; the underlying plan and permanent profile stay intact.
    matching_override=next((item["payload"] for item in REPOSITORIES.weekly_overrides(record["athlete_id"])
                            if item["payload"].get("week_start")==monday.isoformat()), None)
    if matching_override:
        from rowing_plan.weekly_overrides import apply_to_sessions
        sessions=apply_to_sessions(sessions,matching_override)
    enriched_sessions=enrich_sessions(plan_id,sessions)
    days=[]
    for offset in range(7):
        current=monday+timedelta(days=offset); day_sessions=[s for s in enriched_sessions if s["date"]==current.isoformat()]
        state=calendar.get(current.isoformat(),{}).get("state","no_additional_session")
        days.append({"date":current.isoformat(),"day":current.strftime("%A"),"state":state,"sessions":day_sessions})
    return {"plan_id": plan_id,"plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile), "week": monday.isocalendar().week, "week_start":monday.isoformat(), "days": days, "weekly_override_applied":bool(matching_override)}

@app.get("/api/v1/plans/{plan_id}/calendar")
def calendar(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id); profile=REPOSITORIES.get(record["athlete_id"]) or {}
    sessions_by_date={}
    for session in record["plan"].get("sessions",[]): sessions_by_date.setdefault(session["date"],[]).append(session)
    days=[{**item,"sessions":sessions_by_date.get(item["date"],[])} for item in record["plan"].get("calendar_days",[])]
    return {"plan_id":plan_id,"plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile),"days":days,"phases":record["plan"].get("phases",[])}

@app.get("/api/v1/plans/{plan_id}/sessions/detail")
def session_detail(plan_id: str, session_date: date, session_id: str, mode: str, user_id: str = Depends(current_user_id)) -> dict:
    """Mobile display adapter; planning calculations remain in the engine."""
    record=owned_plan(plan_id,user_id)
    session=next((s for s in record["plan"]["sessions"] if s["date"]==session_date.isoformat() and s["session_id"]==session_id and s["mode"]==mode),None)
    if not session: raise HTTPException(404, "Session not found")
    type_map={"on_water":"row_water","erg":"row_erg","strength":"strength","race":"race","treadmill_walk_jog":"alternate_ut2","elliptical":"alternate_ut2","bike":"cross_training"}
    session_type="coached_lesson" if session_id=="COACHED" else type_map.get(mode,"cross_training")
    return {"session_id":session_id,"date":session["date"],"session_type":session_type,"title":session["title"],"primary_band":session.get("band"),"planned_duration_min":session.get("total_cardio_minutes",0),"segments":[{"type":"main","duration_min":session.get("total_cardio_minutes",0),"description":session.get("structure","")}],"erg_targets":{"watts":session.get("target_watts"),"split":session.get("split_guide"),"rate":session.get("rating"),"hr":session.get("hr_range")} if mode=="erg" else None,"water_targets":{"rate":session.get("rating"),"hr":session.get("hr_range"),"technical_cue":session.get("technical_cue"),"note":"Water speed varies with current, wind, steering, boat class, and direction."} if mode=="on_water" else None,"coach_directed":session_id=="COACHED","description":session.get("description",session.get("structure","")),"recovery":session.get("recovery"),"rpe_guidance":session.get("rating")}

@app.post("/api/v1/plans/{plan_id}/sessions/{session_key}/log")
def log_workout(plan_id: str, session_key: str, log: WorkoutLogRequest, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id)
    payload=log.model_dump()
    source=next((s for s in record["plan"].get("sessions",[]) if stable_session_key(s)==session_key),None)
    if not source: raise HTTPException(404,"Session not found")
    proposal=bounded_coached_proposal(record,session_key,payload) if coached_session(source) else {"recommendation":"none","classification":"logged","changes":[]}
    return {"status": "accepted", "log_id": REPOSITORIES.save_log(plan_id, session_key, payload), "session_load_au":session_load_au(payload), "adjustment":proposal}

@app.post("/api/v1/plans/{plan_id}/coached-session-adjustment/apply")
def apply_coached_session_adjustment(plan_id: str, session_key: str, user_id: str = Depends(current_user_id)) -> dict:
    """Create a new version only after the athlete accepts the bounded proposal."""
    record=owned_plan(plan_id,user_id); actual=actual_by_key(plan_id).get(session_key)
    if not actual: raise HTTPException(422,"Log the coached session before reviewing the remaining week.")
    proposal=bounded_coached_proposal(record,session_key,actual)
    if proposal["recommendation"]!="review": return {"status":"no_change","plan_id":plan_id,"proposal":proposal}
    plan=deepcopy(record["plan"]); changes={item["session_key"]:item["suggested"] for item in proposal["changes"]}
    plan["sessions"]=[changes.get(stable_session_key(session),session) for session in plan.get("sessions",[])]
    plan.setdefault("adjustment_provenance",[]).append({"reason":"coached_session_actual","source_session_id":session_key,"source_plan_id":plan_id,"changes":[item["session_key"] for item in proposal["changes"]]})
    new_plan_id=REPOSITORIES.save_plan(record["athlete_id"],plan)
    return {"status":"applied","plan_id":new_plan_id,"proposal":proposal}

@app.get("/api/v1/plans/{plan_id}/logs")
def workout_logs(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_plan(plan_id,user_id)
    logs=REPOSITORIES.logs_for_plan(plan_id)
    return {"plan_id":plan_id,"logs":logs,"load_summary":load_summary(logs)}

@app.post("/api/v1/athletes/{athlete_id}/private-check-ins")
def private_check_in(athlete_id: str, entry: PrivateCheckInRequest, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    entry_id=REPOSITORIES.save_private_check_in(athlete_id,entry.model_dump())
    return {"status":"accepted","entry_id":entry_id,"message":"Private tracking is opt-in and never changes training automatically."}

@app.get("/api/v1/athletes/{athlete_id}/private-check-ins")
def private_check_ins(athlete_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    entries=REPOSITORIES.private_check_ins(athlete_id)
    high=sum(e["payload"].get("symptom_impact")=="high" for e in entries)
    return {"entries":entries,"suggestion":"Review recovery and session placement with your coach if this reflects a repeated personal pattern." if high>=3 else None,"automatic_plan_change":False}

@app.post("/api/v1/athletes/{athlete_id}/weekly-overrides")
def save_weekly_override(athlete_id: str, override: WeeklyOverrideRequest, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    from rowing_plan.weekly_overrides import normalize
    payload=normalize(override.model_dump())
    return {"override_id":REPOSITORIES.save_weekly_override(athlete_id,payload),"scope":payload["scope"]}

@app.get("/api/v1/athletes/{athlete_id}/weekly-overrides")
def weekly_overrides(athlete_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    return {"overrides":REPOSITORIES.weekly_overrides(athlete_id)}

@app.get("/api/v1/race-postings")
def race_postings(user_id: str = Depends(current_user_id)) -> dict:
    account=REPOSITORIES.latest_for_user(user_id); level=(account or {}).get("athlete_profile",{}).get("athlete",{}).get("experience_level","intermediate")
    items=REPOSITORIES.race_postings()
    return {"postings":[{**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]} for item in items if level in item["payload"].get("audience_levels",[])]}

@app.get("/api/v1/admin/race-postings")
def admin_race_postings(user_id: str = Depends(require_coach_admin)) -> dict:
    return {"postings":[{**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]} for item in REPOSITORIES.race_postings()]}

@app.post("/api/v1/admin/race-postings")
def create_race_posting(posting: RacePostingRequest, user_id: str = Depends(require_coach_admin)) -> dict:
    if posting.end_date < posting.start_date: raise HTTPException(422,"Race posting end date must be on or after start date.")
    if not posting.audience_levels: raise HTTPException(422,"Choose at least one rower group.")
    item=REPOSITORIES.create_race_posting(user_id,posting.model_dump())
    return {**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]}

@app.put("/api/v1/admin/race-postings/{posting_id}")
def update_race_posting(posting_id: str, posting: RacePostingRequest, user_id: str = Depends(require_coach_admin)) -> dict:
    if posting.end_date < posting.start_date: raise HTTPException(422,"Race posting end date must be on or after start date.")
    item=REPOSITORIES.update_race_posting(posting_id,posting.model_dump())
    if not item: raise HTTPException(404,"Race posting not found")
    return {**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]}

@app.get("/api/v1/plans/{plan_id}/season")
def season_summary(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id)
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    today_value=date.today()
    phases=record["plan"].get("phases",[])
    current=next((item for item in phases if item["date"]==today_value.isoformat()), next((item for item in phases if item["date"]>=today_value.isoformat()), None))
    races=profile.get("races",[])
    next_race=next((race for race in sorted(races,key=lambda r:r["start_date"]) if date.fromisoformat(race["start_date"])>=today_value),None)
    transitions=[]; prior=None
    for item in phases:
        if item["phase"]!=prior:
            transitions.append(item); prior=item["phase"]
    return {"current_phase":current,"next_race":next_race,"days_to_next_race":(date.fromisoformat(next_race["start_date"])-today_value).days if next_race else None,"transitions":transitions}

@app.get("/api/v1/plans/{plan_id}/excel")
def excel(plan_id: str, user_id: str = Depends(current_user_id)) -> StreamingResponse:
    record = owned_plan(plan_id, user_id)
    profile=REPOSITORIES.get(record["athlete_id"])
    if not profile: raise HTTPException(404, "Athlete not found")
    buffer=BytesIO(build_workbook(profile, record["plan"]))
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=rowing-plan.xlsx"})
