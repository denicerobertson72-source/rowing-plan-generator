"""Test-only disposable API fixture for browser acceptance runs."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories

REAL_DB = (Path(__file__).resolve().parents[1] / "data" / "rowing_plan.sqlite3").resolve()

def synthetic_profile() -> dict:
    return {
        "athlete": {"display_name": "Synthetic Step 6B Rower", "experience_level": "experienced", "current_rowing_sessions_per_week": 4, "recent_training_consistency": "consistent", "current_approx_weekly_rowing_minutes": 220},
        "season": {"start_date": "2026-09-01", "end_date": "2026-11-08", "current_weekly_endurance_minutes": 180, "target_peak_weekly_endurance_minutes": 270},
        "tests": {"resting_hr": 58, "max_hr": 177, "erg_2k_seconds": None, "multi_duration_power_tests": {}},
        "weekly_availability": [{"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]} for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")],
        "races": [
            {"event_name": "Synthetic Sep C", "start_date": "2026-09-26", "end_date": "2026-09-26", "priority": "C", "race_type": "head_5k"},
            {"event_name": "Synthetic Oct B", "start_date": "2026-10-17", "end_date": "2026-10-17", "priority": "B", "race_type": "head_5k"},
            {"event_name": "Synthetic Nov A", "start_date": "2026-11-07", "end_date": "2026-11-08", "priority": "A", "race_type": "head_5k"},
        ],
        "recurring_activities": [
            {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
            {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[]},
            {"activity_id":"lift","activity_type":"strength","sessions_per_week":2,"scheduling_status":"preferred","fixed_days":[],"preferred_days":["monday","friday"],"allowed_days":["tuesday","thursday"],"prohibited_days":[],"same_day_rules":{"rowing_allowed":False}},
            {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["sunday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        ],
    }

@contextmanager
def disposable_browser_fixture():
    with tempfile.TemporaryDirectory(prefix="rowing-plan-step6b-") as directory:
        db_path = Path(directory) / "acceptance.sqlite3"
        if db_path.resolve() == REAL_DB: raise RuntimeError("Step 6B fixture refused the real development database")
        previous=os.environ.get("ROWING_PLAN_DB_PATH")
        os.environ["ROWING_PLAN_DB_PATH"]=str(db_path)
        REPOSITORIES._instance=None
        profile=synthetic_profile(); repo=SQLiteRepositories(db_path); athlete_id=repo.create(profile,"development-user")
        config=json.loads((Path(__file__).resolve().parents[3] / "config" / "defaults.json").read_text())
        plan=generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config)); plan_id=repo.save_plan(athlete_id,plan)
        try: yield {"database":db_path,"athlete_id":athlete_id,"plan_id":plan_id}
        finally:
            REPOSITORIES._instance=None
            if previous is None: os.environ.pop("ROWING_PLAN_DB_PATH",None)
            else: os.environ["ROWING_PLAN_DB_PATH"]=previous
