"""Recurring commitments replace fixed weekday assumptions without breaking legacy profiles."""
from __future__ import annotations
from uuid import uuid4
import hashlib
import json

def schedule_signature(profile: dict) -> str:
    """Stable fingerprint of fields that change future-plan placement."""
    source={
        "recurring_activities":migrate_legacy_availability(profile),
        "season":{key:profile.get("season",{}).get(key) for key in ("start_date","end_date")},
        "races":profile.get("races",[]),
        "availability":profile.get("weekly_availability",[]),
        "preferences":profile.get("preferences",{}).get("workout_structure_preference"),
    }
    return hashlib.sha256(json.dumps(source,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()[:16]

def migrate_legacy_availability(profile: dict) -> list[dict]:
    activities=profile.get("recurring_activities")
    if activities is not None: return activities
    days=profile.get("weekly_availability",[])
    lifts=[d["weekday"] for d in days if d.get("heavy_lifting")]
    rests=[d["weekday"] for d in days if d.get("fixed_rest")]
    items=[]
    if lifts: items.append({"activity_id":str(uuid4()),"activity_type":"strength","sessions_per_week":len(lifts),"scheduling_status":"fixed","fixed_days":lifts,"preferred_days":[],"allowed_days":lifts,"prohibited_days":[],"planner_may_choose_day":False,"same_day_rules":{"rowing_allowed":all(d.get("row_on_lifting_day",True) for d in days if d["weekday"] in lifts),"strength_allowed":True,"alternate_ut2_allowed":any(d.get("alternate_ut2_allowed") for d in days if d["weekday"] in lifts),"second_hard_session_allowed":False},"race_week_mobility":"locked","notes":"Migrated lifting commitments."})
    if rests: items.append({"activity_id":str(uuid4()),"activity_type":"rest","sessions_per_week":len(rests),"scheduling_status":"fixed","fixed_days":rests,"preferred_days":[],"allowed_days":rests,"prohibited_days":[],"planner_may_choose_day":False,"same_day_rules":{"rowing_allowed":False,"strength_allowed":False,"alternate_ut2_allowed":False,"second_hard_session_allowed":False},"race_week_mobility":"locked","notes":"Migrated rest commitments."})
    return items

def validate_recurring_activities(profile: dict) -> list[str]:
    errors=[]
    items=migrate_legacy_availability(profile)
    fixed_days={day for item in items if item.get("scheduling_status")=="fixed" for day in item.get("fixed_days",[])}
    seen_fixed=set()
    for item in items:
        allowed=set(item.get("allowed_days",[]))|set(item.get("fixed_days",[]))|set(item.get("preferred_days",[]))
        if item.get("sessions_per_week",0)>len(allowed): errors.append(f"{item.get('activity_type','Activity')} requests more weekly sessions than allowed days.")
        if set(item.get("prohibited_days",[]))&allowed: errors.append(f"{item.get('activity_type','Activity')} includes a prohibited day.")
        if item.get("scheduling_status")=="fixed":
            overlap=seen_fixed&set(item.get("fixed_days",[]))
            if overlap: errors.append(f"Fixed commitments overlap on {', '.join(sorted(overlap))}.")
            seen_fixed.update(item.get("fixed_days",[]))
        if item.get("scheduling_status")!="fixed":
            free_days=allowed-fixed_days
            if item.get("sessions_per_week",0)>len(free_days): errors.append(f"{item.get('activity_type','Activity')} cannot be placed its requested number of times without sharing a fixed commitment day.")
    return errors
