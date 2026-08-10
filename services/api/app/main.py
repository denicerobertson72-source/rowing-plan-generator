"""API-first adapter around the preserved deterministic planning engine."""
from __future__ import annotations
import json
import sys
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from rowing_plan import PLANNER_VERSION
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan
from rowing_plan.validators import hard_constraint_errors, validate_profile
from rowing_plan.workbook import build_workbook
from .repositories import REPOSITORIES
from .schemas import ApiHealth, AthleteCreateRequest, AthleteResponse, PlanGenerationRequest, PlanResponse, RegenerateRequest, WorkoutLogRequest

CONFIG = json.loads((ROOT / "config/defaults.json").read_text())
app = FastAPI(title="Rowing Plan API", version="0.4.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def build_plan(request: PlanGenerationRequest) -> dict:
    errors = validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors": errors})
    bands = build_intensity_profile(request.athlete_profile, CONFIG)
    power = build_power_profile(request.athlete_profile, CONFIG)
    plan = generate_plan(request.athlete_profile, CONFIG, bands, power, request.locked_sessions)
    hard_errors = hard_constraint_errors(plan, request.athlete_profile)
    if hard_errors: raise HTTPException(status_code=422, detail={"constraint_errors": hard_errors})
    return plan

@app.get("/api/v1/health", response_model=ApiHealth)
def health() -> ApiHealth: return ApiHealth(status="ok", api_version="v1", planner_version=PLANNER_VERSION)

@app.post("/api/v1/plans/generate", response_model=PlanResponse)
def generate(request: PlanGenerationRequest) -> PlanResponse:
    plan = build_plan(request)
    athlete_id=REPOSITORIES.create(request.athlete_profile)
    return PlanResponse(plan_id=REPOSITORIES.save_plan(athlete_id, plan), plan=plan)

@app.post("/api/v1/athletes", response_model=AthleteResponse)
def create_athlete(request: AthleteCreateRequest) -> AthleteResponse:
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    athlete_id=REPOSITORIES.create(request.athlete_profile, request.user_id)
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=request.athlete_profile)

@app.get("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def get_athlete(athlete_id: str) -> AthleteResponse:
    profile=REPOSITORIES.get(athlete_id)
    if not profile: raise HTTPException(404, "Athlete not found")
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=profile)

@app.put("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def update_athlete(athlete_id: str, request: AthleteCreateRequest) -> AthleteResponse:
    if not REPOSITORIES.get(athlete_id): raise HTTPException(404, "Athlete not found")
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    REPOSITORIES.save(athlete_id, request.athlete_profile)
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=request.athlete_profile)

@app.post("/api/v1/athletes/{athlete_id}/plans/generate", response_model=PlanResponse)
def generate_for_athlete(athlete_id: str, request: RegenerateRequest) -> PlanResponse:
    profile=REPOSITORIES.get(athlete_id)
    if not profile: raise HTTPException(404, "Athlete not found")
    plan=build_plan(PlanGenerationRequest(athlete_profile=profile, locked_sessions=request.locked_sessions))
    return PlanResponse(plan_id=REPOSITORIES.save_plan(athlete_id,plan), plan=plan)

@app.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str) -> dict:
    record=REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404, "Plan not found")
    return record

@app.get("/api/v1/plans/{plan_id}/today")
def today(plan_id: str, on: Optional[date] = None) -> dict:
    record = REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404, "Plan not found")
    target = (on or date.today()).isoformat()
    sessions = [s for s in record["plan"]["sessions"] if s["date"] == target]
    return {"plan_id": plan_id, "date": target, "sessions": sessions, "cached_at": date.today().isoformat()}

@app.get("/api/v1/plans/{plan_id}/week")
def week(plan_id: str, week_number: int) -> dict:
    record = REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404, "Plan not found")
    sessions = [s for s in record["plan"]["sessions"] if date.fromisoformat(s["date"]).isocalendar().week == week_number]
    if not sessions: return {"plan_id": plan_id, "week": week_number, "days": []}
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    availability={item["weekday"]:item for item in profile.get("weekly_availability",[])}
    first=min(date.fromisoformat(s["date"]) for s in sessions)
    monday=first-timedelta(days=first.weekday())
    days=[]
    for offset in range(7):
        current=monday+timedelta(days=offset); day_sessions=[s for s in sessions if s["date"]==current.isoformat()]
        rule=availability.get(current.strftime("%A").lower(),{})
        state="rest" if rule.get("fixed_rest") or not rule.get("available",True) else "planned"
        days.append({"date":current.isoformat(),"day":current.strftime("%A"),"state":state,"sessions":day_sessions})
    return {"plan_id": plan_id, "week": week_number, "days": days}

@app.get("/api/v1/plans/{plan_id}/sessions/detail")
def session_detail(plan_id: str, session_date: date, session_id: str, mode: str) -> dict:
    """Mobile display adapter; planning calculations remain in the engine."""
    record=REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404, "Plan not found")
    session=next((s for s in record["plan"]["sessions"] if s["date"]==session_date.isoformat() and s["session_id"]==session_id and s["mode"]==mode),None)
    if not session: raise HTTPException(404, "Session not found")
    type_map={"on_water":"row_water","erg":"row_erg","strength":"strength","race":"race","treadmill_walk_jog":"alternate_ut2","elliptical":"alternate_ut2","bike":"cross_training"}
    session_type="coached_lesson" if session_id=="COACHED" else type_map.get(mode,"cross_training")
    return {"session_id":session_id,"date":session["date"],"session_type":session_type,"title":session["title"],"primary_band":session.get("band"),"planned_duration_min":session.get("total_cardio_minutes",0),"segments":[{"type":"main","duration_min":session.get("total_cardio_minutes",0),"description":session.get("structure","")}],"erg_targets":{"watts":session.get("target_watts"),"split":session.get("split_guide"),"rate":session.get("rating"),"hr":session.get("hr_range")} if mode=="erg" else None,"water_targets":{"rate":session.get("rating"),"hr":session.get("hr_range"),"technical_cue":session.get("technical_cue"),"note":"Water speed varies with current, wind, steering, boat class, and direction."} if mode=="on_water" else None,"coach_directed":session_id=="COACHED","description":session.get("description",session.get("structure","")),"recovery":session.get("recovery"),"rpe_guidance":session.get("rating")}

@app.post("/api/v1/plans/{plan_id}/sessions/{session_key}/log")
def log_workout(plan_id: str, session_key: str, log: WorkoutLogRequest) -> dict:
    if not REPOSITORIES.get_plan(plan_id): raise HTTPException(404, "Plan not found")
    return {"status": "accepted", "log_id": REPOSITORIES.save_log(plan_id, session_key, log.model_dump())}

@app.get("/api/v1/plans/{plan_id}/logs")
def workout_logs(plan_id: str) -> dict:
    if not REPOSITORIES.get_plan(plan_id): raise HTTPException(404, "Plan not found")
    return {"plan_id":plan_id,"logs":REPOSITORIES.logs_for_plan(plan_id)}

@app.get("/api/v1/plans/{plan_id}/season")
def season_summary(plan_id: str) -> dict:
    record=REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404, "Plan not found")
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
def excel(plan_id: str) -> StreamingResponse:
    record = REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404, "Plan not found")
    profile=REPOSITORIES.get(record["athlete_id"])
    if not profile: raise HTTPException(404, "Athlete not found")
    buffer=BytesIO(build_workbook(profile, record["plan"]))
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=rowing-plan.xlsx"})
