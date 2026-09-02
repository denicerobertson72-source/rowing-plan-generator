"""API-first adapter around the preserved deterministic planning engine."""
from __future__ import annotations
import json
import os
import sys
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException
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
from .auth import current_user_id
from rowing_plan.training_load import load_summary, session_load_au
from rowing_plan.recurring_activities import schedule_signature
from .schemas import ApiHealth, AthleteCreateRequest, AthleteResponse, PlanGenerationRequest, PlanResponse, PrivateCheckInRequest, RacePostingRequest, RegenerateRequest, WeeklyOverrideRequest, WorkoutLogRequest

CONFIG = json.loads((ROOT / "config/defaults.json").read_text())
app = FastAPI(title="Rowing Plan API", version="0.4.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
allowed_origins=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def prevent_dynamic_api_caching(request, call_next):
    response=await call_next(request)
    if request.url.path.startswith("/api/v1/") and request.method=="GET": response.headers["Cache-Control"]="no-store"
    return response

def build_plan(request: PlanGenerationRequest) -> dict:
    errors = validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors": errors})
    bands = build_intensity_profile(request.athlete_profile, CONFIG)
    power = build_power_profile(request.athlete_profile, CONFIG)
    try: plan = generate_plan(request.athlete_profile, CONFIG, bands, power, request.locked_sessions)
    except ValueError as error: raise HTTPException(status_code=422, detail={"planning_conflicts":[str(error)]}) from error
    hard_errors = hard_constraint_errors(plan, request.athlete_profile)
    if hard_errors: raise HTTPException(status_code=422, detail={"constraint_errors": hard_errors})
    return plan

def owned_athlete(athlete_id: str, user_id: str) -> dict:
    profile=REPOSITORIES.get(athlete_id)
    if not profile: raise HTTPException(404,"Athlete not found")
    if REPOSITORIES.athlete_owner(athlete_id) != user_id: raise HTTPException(403,"This athlete belongs to another account.")
    return profile
def owned_plan(plan_id: str, user_id: str) -> dict:
    record=REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404,"Plan not found")
    if REPOSITORIES.plan_owner(plan_id) != user_id: raise HTTPException(403,"This plan belongs to another account.")
    return record
def plan_needs_update(record: dict, profile: dict) -> bool:
    return record["plan"].get("schedule_signature") != schedule_signature(profile)
def require_coach_admin(user_id: str = Depends(current_user_id)) -> str:
    allowed={value.strip() for value in os.getenv("COACH_ADMIN_USER_IDS","").split(",") if value.strip()}
    if user_id not in allowed: raise HTTPException(403,"Coach/admin access is required for race postings.")
    return user_id

@app.get("/api/v1/health", response_model=ApiHealth)
def health() -> ApiHealth: return ApiHealth(status="ok", api_version="v1", planner_version=PLANNER_VERSION)

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
    athlete_id=REPOSITORIES.create(request.athlete_profile, user_id)
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=request.athlete_profile)

@app.get("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def get_athlete(athlete_id: str, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    profile=owned_athlete(athlete_id,user_id)
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=profile)

@app.get("/api/v1/account/athlete")
def get_current_athlete(user_id: str = Depends(current_user_id)) -> dict:
    record=REPOSITORIES.latest_for_user(user_id)
    if not record: raise HTTPException(404,"No Athlete Profile is associated with this account.")
    return record

@app.put("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def update_athlete(athlete_id: str, request: AthleteCreateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    owned_athlete(athlete_id,user_id)
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    REPOSITORIES.save(athlete_id, request.athlete_profile)
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=REPOSITORIES.get(athlete_id) or request.athlete_profile)

@app.post("/api/v1/athletes/{athlete_id}/plans/generate", response_model=PlanResponse)
def generate_for_athlete(athlete_id: str, request: RegenerateRequest, user_id: str = Depends(current_user_id)) -> PlanResponse:
    profile=owned_athlete(athlete_id,user_id)
    previous=REPOSITORIES.latest_plan_for_athlete(athlete_id)
    locked=list(request.locked_sessions)
    if previous:
        completed={entry["session_key"] for entry in REPOSITORIES.logs_for_plan(previous["plan_id"]) if entry["payload"].get("status")=="completed"}
        locked.extend(session for session in previous["plan"].get("sessions",[]) if f'{session["date"]}:{session.get("session_id")}:{session.get("mode")}' in completed)
    plan=build_plan(PlanGenerationRequest(athlete_profile=profile, locked_sessions=locked))
    return PlanResponse(plan_id=REPOSITORIES.save_plan(athlete_id,plan), plan=plan)

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
    return {"plan_id": plan_id, "plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile),"date": target, "sessions": sessions, "cached_at": date.today().isoformat()}

@app.get("/api/v1/plans/{plan_id}/week")
def week(plan_id: str, week_number: int, user_id: str = Depends(current_user_id)) -> dict:
    record = owned_plan(plan_id,user_id)
    sessions = [s for s in record["plan"]["sessions"] if date.fromisoformat(s["date"]).isocalendar().week == week_number]
    if not sessions: return {"plan_id": plan_id, "week": week_number, "days": []}
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    calendar={item["date"]:item for item in record["plan"].get("calendar_days",[])}
    first=min(date.fromisoformat(s["date"]) for s in sessions)
    monday=first-timedelta(days=first.weekday())
    # The newest saved override for this week takes precedence.  It is applied
    # only to this read model; the underlying plan and permanent profile stay intact.
    matching_override=next((item["payload"] for item in REPOSITORIES.weekly_overrides(record["athlete_id"])
                            if item["payload"].get("week_start")==monday.isoformat()), None)
    if matching_override:
        from rowing_plan.weekly_overrides import apply_to_sessions
        sessions=apply_to_sessions(sessions,matching_override)
    days=[]
    for offset in range(7):
        current=monday+timedelta(days=offset); day_sessions=[s for s in sessions if s["date"]==current.isoformat()]
        state=calendar.get(current.isoformat(),{}).get("state","no_additional_session")
        days.append({"date":current.isoformat(),"day":current.strftime("%A"),"state":state,"sessions":day_sessions})
    return {"plan_id": plan_id,"plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile), "week": week_number, "days": days, "weekly_override_applied":bool(matching_override)}

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
    owned_plan(plan_id,user_id)
    payload=log.model_dump()
    return {"status": "accepted", "log_id": REPOSITORIES.save_log(plan_id, session_key, payload), "session_load_au":session_load_au(payload)}

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
