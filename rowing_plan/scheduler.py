"""Constraint-first deterministic season scheduler."""
from __future__ import annotations
from datetime import date, timedelta, datetime
from collections import defaultdict
from .periodization import phase_for_day, parse, build_phases, build_season_phases, build_weekly_training_intents, PLANNING_MODEL_VERSION
from .session_selector import load_library, select_session
from .power_profile import target_for_band
from .evidence import METHODOLOGY_STATEMENT, RULES
from .recurring_activities import migrate_legacy_availability, schedule_signature
from .schedule_scoring import choose
from .conversions import watts_to_split_seconds, format_split
from .session_selection import VERSION as SELECTION_VERSION, assign_week_roles, select_and_instantiate
from .load_transformations import VERSION as TRANSFORMATION_VERSION, transform

WEEKDAY=["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
def _availability(profile): return {x["weekday"]:x for x in profile["weekly_availability"]}
def _race(day,races): return next((r for r in races if parse(r["start_date"])<=day<=parse(r["end_date"])),None)
def _recurring_commitments(profile, start, end):
    """Choose recurring placements for each calendar week before sessions are built.

    Legacy profiles keep their weekday matrix unchanged.  A profile with the
    v0.6 model instead uses fixed/preferred/flexible activity cards as the
    source of strength, coaching, and rest commitments.
    """
    activities=profile.get("recurring_activities")
    if activities is None: return {}, []
    commitments=defaultdict(list); moves=[]; week_start=start-timedelta(days=start.weekday())
    while week_start<=end:
        fixed_days={day for activity in activities if activity.get("scheduling_status")=="fixed" for day in activity.get("fixed_days",[])}
        # Tuesday is the engine's normal quality-row candidate; scorer avoids
        # placing movable stress there when another athlete-approved day exists.
        quality_days={"tuesday"}
        occupied_days=set()
        # Fixed commitments establish the weekly frame before any preferences
        # are scored.  Movable cards are then placed one-by-one without overlap.
        # Flexible rest is placed after strength and coaching commitments. This
        # lets its candidate score preserve independent-row recovery spacing
        # instead of prematurely consuming the only useful gap.
        ordered=sorted(activities,key=lambda item:(item.get("scheduling_status")!="fixed",item.get("activity_type")=="rest"))
        for activity in ordered:
            placement=choose(activity,quality_days,fixed_days,occupied_days)
            requested=activity.get("sessions_per_week",1)
            if len(placement["scheduled_days"]) != requested:
                raise ValueError(f"{activity.get('activity_type','Activity')} cannot be placed {requested} time(s) in a full week without conflicting with your other commitments.")
            moves.append({"week_start":week_start.isoformat(),"activity_id":activity.get("activity_id"),"activity_type":activity.get("activity_type"),**placement})
            for weekday in placement["scheduled_days"]:
                try: offset=WEEKDAY.index(weekday)
                except ValueError: continue
                current=week_start+timedelta(days=offset)
                if start<=current<=end: commitments[current.isoformat()].append(activity)
            occupied_days.update(placement["scheduled_days"])
        week_start+=timedelta(days=7)
    return commitments,moves
def _commitment(activity_type, day, phase, avail, activity):
    if activity_type=="strength":
        return {"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":activity.get("scheduling_status")=="fixed","mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Scheduled strength commitment."}
    return {"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":activity.get("scheduling_status")=="fixed","mode":"on_water","session_id":"COACHED","title":"Private coaching" if activity_type=="private_coaching" else "Coached row","total_cardio_minutes":min(50,avail.get("max_training_minutes",50)),"rowing_minutes":min(50,avail.get("max_training_minutes",50)),"quality_minutes":0,"band":"UT2/UT1","structure":"Coach-led technique and aerobic work.","warning":"Coach instructions take priority."}
def _session(day, phase, avail, library, band, power, race_type, structure_preference="varied", fixed=False, title=None, role=None, experience="intermediate", history=None):
    mode=next((m for m in avail.get("rowing_modes",[]) if m in ("on_water","erg")),"erg")
    minutes=min(avail.get("max_training_minutes",60), 60 if band in ("UT2","UT1") else 50)
    selected=select_and_instantiate(role=role,experience=experience,phase=phase,race_type=race_type,mode=mode,minutes=minutes,preference=structure_preference,history=history or []) if role else None
    if selected:
        archetype=selected["archetype"]; band=archetype["primary_band"]
        anchor=target_for_band(power,band) if mode=="erg" else None
        watts=round((anchor["target_watts_low"]+anchor["target_watts_high"])/2,1) if anchor else None
        rate=archetype["rate_range_spm"]
        return {"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":fixed,"mode":mode,"session_id":archetype["archetype_id"],"title":title or archetype["name"],"total_cardio_minutes":selected["total_minutes"],"rowing_minutes":selected["total_minutes"],"quality_minutes":selected["total_minutes"] if band in ("AT","TR","AN","PP") else 0,"band":band,"structure":f"{selected['repetitions']} × {selected['work_interval_duration']} min {band}; {selected['recovery_duration']} min easy recovery.","recovery":archetype["minimum_recovery_guidance"],"technical_cue":"Maintain posture and connection as the session develops.","rate_guide":rate,"source_basis_ids":archetype["source_ids"],"power_target_method":anchor["formula"] if anchor else "Intensity provider / HRR-RPE guidance","source_anchor":anchor["source_test"] if anchor else None,"target_watts":watts,"split_guide":format_split(watts_to_split_seconds(watts)) if watts else None,"confidence":anchor["confidence"] if anchor else "low","assumptions":anchor["assumptions"] if anchor else ["Follow rate, breathing, and RPE where exact power is unavailable."],"archetype_id":archetype["archetype_id"],"session_role":role,"phase_role":phase,"progression_dimension":selected["progression_dimension"],"selection_reason":selected["selection_reason"],"preference_effect":selected["preference_effect"],"selection_reason_codes":["weekly_intent_role","deterministic_archetype_selection"],"candidate_scores":selected["candidate_scores"],"session_fingerprint":selected["fingerprint"]}
    template=select_session(library,band,phase,race_type,[mode],minutes,structure_preference) or select_session(library,band,"all",race_type,[mode],minutes,structure_preference)
    if not template: return None
    anchor=target_for_band(power,band) if mode=="erg" else None
    watts = round((anchor["target_watts_low"]+anchor["target_watts_high"])/2,1) if anchor else None
    return {"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":fixed,"mode":mode,"session_id":template["session_id"],"title":title or template["title"],"total_cardio_minutes":minutes,"rowing_minutes":minutes,"quality_minutes":minutes if band in ("AT","TR","AN","PP") else 0,"band":band,"structure":template["work_structure"],"recovery":template["recovery_structure"],"technical_cue":template["technical_cues"][0],"rate_guide":template.get("spm_guidance"),"source_basis_ids":template["source_basis_ids"],"power_target_method":anchor["formula"] if anchor else "Intensity provider / HRR-RPE guidance", "source_anchor":anchor["source_test"] if anchor else None,"target_watts":watts,"split_guide":format_split(watts_to_split_seconds(watts)) if watts else None,"confidence":anchor["confidence"] if anchor else "low","assumptions":anchor["assumptions"] if anchor else ["Follow rate, breathing, and RPE where exact power is unavailable."]}

def _calendar_days(profile, start, end, commitments, modern_schedule):
    """Persist an explicit day state; an empty day is not automatically rest."""
    availability=_availability(profile); result=[]; day=start
    while day<=end:
        activities=commitments.get(day.isoformat(),[]) if modern_schedule else []
        designated_rest=any(item.get("activity_type")=="rest" for item in activities)
        legacy=availability.get(WEEKDAY[day.weekday()],{})
        unavailable=(not modern_schedule and not legacy.get("available",True) and not legacy.get("fixed_rest",False))
        result.append({"date":day.isoformat(),"designated_rest":designated_rest,"unavailable":unavailable,"state":"designated_rest" if designated_rest else "unavailable" if unavailable else "no_additional_session","commitments":[{"activity_id":item.get("activity_id"),"activity_type":item.get("activity_type")} for item in activities]})
        day+=timedelta(days=1)
    return result

def _validate_weekly_frequencies(profile, start, end, calendar_days, phases):
    """Reject a normal complete week that loses a requested recurring session."""
    activities=profile.get("recurring_activities")
    if activities is None: return []
    expected={item.get("activity_type"):item.get("sessions_per_week",0) for item in activities}
    phase_by_date={item["date"]:item["phase"] for item in phases}; errors=[]; exceptions=[]
    monday=start-timedelta(days=start.weekday())
    while monday+timedelta(days=6)<=end:
        if monday<start:
            monday+=timedelta(days=7); continue
        week=[item for item in calendar_days if monday<=date.fromisoformat(item["date"])<=monday+timedelta(days=6)]
        phase_set={phase_by_date.get(item["date"]) for item in week}
        if phase_set & {"race","taper_sharpen","race_recovery"}:
            exceptions.append({"week_start":monday.isoformat(),"reason_code":"race_period_exception"})
            monday+=timedelta(days=7); continue
        actual={}
        for item in week:
            for commitment in item["commitments"]:
                kind=commitment["activity_type"]; actual[kind]=actual.get(kind,0)+1
        for kind,count in expected.items():
            if actual.get(kind,0)!=count:
                errors.append(f"Unable to place {count} requested {kind.replace('_',' ')} session(s) in the week of {monday.isoformat()} while preserving your other commitments.")
        monday+=timedelta(days=7)
    return errors,exceptions

def _weekly_volume_feasibility(intents, sessions, tolerance=0.10):
    """Report residual volume without inflating quality work to hit a number."""
    results=[]; scalable={"AEROBIC_BASE","LONG_AEROBIC","TECHNIQUE_EASY","AEROBIC_STRENGTH"}
    for intent in intents:
        start=date.fromisoformat(intent["week_start"]); end=start+timedelta(days=6)
        rows=[s for s in sessions if s.get("rowing_minutes",0) and start<=date.fromisoformat(s["date"])<=end]
        actual=sum(s["rowing_minutes"] for s in rows); target=intent["target_total_rowing_minutes"]; residual=target-actual; within=abs(residual)<=target*tolerance
        scalable_sessions=[s for s in rows if s.get("session_role") in scalable]
        if within: status,reason="within_tolerance","Planned rowing volume is within the configured weekly tolerance."
        elif residual>0 and not scalable_sessions: status,reason="infeasible","The remaining rowing opportunity is race-specific/high intensity; no scalable aerobic role can absorb residual volume without distorting session quality."
        elif residual>0: status,reason="needs_reconciliation","A scalable aerobic role is available for bounded re-instantiation or reselection."
        else: status,reason="above_target","Committed or generated rowing volume exceeds the target tolerance."
        results.append({"week_start":intent["week_start"],"target_rowing_minutes":target,"planned_rowing_minutes":actual,"residual_rowing_minutes":residual,"tolerance_minutes":round(target*tolerance),"status":status,"reason":reason,"scalable_session_dates":[s["date"] for s in scalable_sessions]})
    return results

def _reconcile_low_intensity_volume(intents, sessions, tolerance=0.10):
    """Boundedly close positive rowing-volume residuals using only easy rows."""
    initial=_weekly_volume_feasibility(intents,sessions,tolerance); adjustments=[]
    for row in initial:
        if row["status"] != "needs_reconciliation": continue
        start=date.fromisoformat(row["week_start"]); end=start+timedelta(days=6); remaining=row["residual_rowing_minutes"]-row["tolerance_minutes"]
        candidates=[s for s in sessions if start<=date.fromisoformat(s["date"])<=end and s.get("session_role") in {"AEROBIC_BASE","LONG_AEROBIC","TECHNIQUE_EASY","AEROBIC_STRENGTH"} and s.get("band") in {"UT2","UT3","UT1"}]
        for session in sorted(candidates,key=lambda s:(s.get("session_role")!="LONG_AEROBIC",s["date"])):
            if remaining<=0: break
            amount=min(20,remaining); session["rowing_minutes"]+=amount; session["total_cardio_minutes"]+=amount
            session["structure"]+=f"; plus {amount} min continuous easy rowing for weekly aerobic-volume reconciliation."
            session.setdefault("reconciliation_adjustments",[]).append({"type":"low_intensity_extension","minutes":amount,"reason":"bounded_rowing_volume_reconciliation"})
            fingerprint=session.get("session_fingerprint")
            if fingerprint: fingerprint["reconciliation_extension_minutes"]=fingerprint.get("reconciliation_extension_minutes",0)+amount
            adjustments.append({"week_start":row["week_start"],"date":session["date"],"minutes":amount,"role":session.get("session_role")})
            remaining-=amount
    final=_weekly_volume_feasibility(intents,sessions,tolerance); output=[]
    for before,after in zip(initial,final):
        changed=[item for item in adjustments if item["week_start"]==after["week_start"]]
        if after["status"]=="needs_reconciliation":
            after["status"]="infeasible_with_reason"; after["reason"]="Bounded low-intensity reconciliation could not close the residual without distorting session roles."
        elif changed: after["status"]="reconciled"; after["reason"]="Bounded low-intensity rowing extensions reconciled the weekly residual."
        elif after["status"] in {"above_target","infeasible"}: after["status"]="infeasible_with_reason"; after["reason"]="Committed or quality-session rowing leaves no safe low-intensity reconciliation path."
        after.update({"original_planned_rowing_minutes":before["planned_rowing_minutes"],"adjustments":changed,"final_rowing_minutes":after["planned_rowing_minutes"],"final_residual_rowing_minutes":after["residual_rowing_minutes"],"final_status":after["status"],"explanation":after["reason"]})
        output.append(after)
    return output

def _hard_session_spacing(sessions):
    """Explain unavoidable adjacent independent hard-row pairs for diagnostics."""
    hard={"THRESHOLD","RACE_PACE","SPRINT_POWER"}
    independent=sorted((s for s in sessions if s.get("session_role") in hard and s.get("band") in {"AT","TR","AN","PP"}),key=lambda s:s["date"])
    findings=[]
    for left,right in zip(independent,independent[1:]):
        if (date.fromisoformat(right["date"])-date.fromisoformat(left["date"])).days==1:
            findings.append({"dates":[left["date"],right["date"]],"roles":[left["session_role"],right["session_role"]],"status":"unavoidable_constraints","reason":"No candidate schedule separated the independent hard sessions without violating a higher-priority commitment."})
    return findings

def _ordinary_row_dates(profile, start, end, commitments, modern_schedule, intents):
    """Choose the ordinary rowing dates needed to meet the weekly prescription.

    Availability remains a capacity constraint.  It is not an instruction to
    fill every open day: coached rows count toward the prescribed rowing
    frequency, then ordinary rows fill only the remaining weekly allowance.
    This is deliberately date placement only; band/template selection remains
    on the established path below.
    """
    availability = _availability(profile)
    races = profile.get("races", [])
    target_by_week = {item["week_start"]: item["target_rowing_sessions"] for item in intents}
    selected = set()
    week_start = start - timedelta(days=start.weekday())
    while week_start <= end:
        coached_rows = 0
        ordinary_candidates = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            if not start <= day <= end or _race(day, races):
                continue
            activities = commitments.get(day.isoformat(), []) if modern_schedule else []
            rest = any(item.get("activity_type") == "rest" for item in activities)
            strength = next((item for item in activities if item.get("activity_type") == "strength"), None)
            coached = any(item.get("activity_type") in ("private_coaching", "coached_row") for item in activities)
            legacy = availability.get(WEEKDAY[day.weekday()], {})
            unavailable = not modern_schedule and (not legacy.get("available", False) or legacy.get("fixed_rest", False))
            strength_blocks = bool(strength and not strength.get("same_day_rules", {}).get("rowing_allowed", True))
            strength_blocks = strength_blocks or (not modern_schedule and bool(legacy.get("heavy_lifting")) and not legacy.get("row_on_lifting_day", True))
            if rest or unavailable or strength_blocks:
                continue
            if coached:
                coached_rows += 1
            else:
                ordinary_candidates.append(day.isoformat())
        ordinary_needed = max(0, target_by_week.get(week_start.isoformat(), len(ordinary_candidates) + coached_rows) - coached_rows)
        selected.update(ordinary_candidates[:ordinary_needed])
        week_start += timedelta(days=7)
    return selected

def generate_plan(profile: dict, config: dict, bands: list[dict], power: dict, locked_sessions: list[dict] | None = None) -> dict:
    library=load_library(); avails=_availability(profile); races=profile.get("races",[]); locked={(s["date"],s.get("session_id")):s for s in (locked_sessions or [])}; sessions=[]; warnings=[]
    start,end=parse(profile["season"]["start_date"]),parse(profile["season"]["end_date"]); commitments,schedule_moves=_recurring_commitments(profile,start,end); modern_schedule=profile.get("recurring_activities") is not None
    phases=build_phases(profile); season_phases=build_season_phases(profile); weekly_training_intents=build_weekly_training_intents(profile,season_phases,commitments,modern_schedule)
    ordinary_row_dates=_ordinary_row_dates(profile,start,end,commitments,modern_schedule,weekly_training_intents)
    day_roles={}
    for intent in weekly_training_intents:
        week_start=date.fromisoformat(intent["week_start"]); dates=[d for d in ordinary_row_dates if week_start<=date.fromisoformat(d)<=week_start+timedelta(days=6)]
        next_race_type=next((r.get("race_type","head_5k") for r in races if parse(r["end_date"])>=week_start),"head_5k")
        day_roles.update(assign_week_roles(dates,intent,next_race_type))
    selection_history=[]; day=start
    while day<=end:
        phase,next_race=phase_for_day(day,races); a=avails.get(WEEKDAY[day.weekday()],{}); race=_race(day,races); key_race_type=(next_race or races[0] if races else {}).get("race_type","head_5k")
        if race:
            starts=int(race.get("expected_starts",1)); estimated=starts*20
            sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":"race","fixed":True,"mode":"race","session_id":"RACE","title":race["event_name"],"total_cardio_minutes":estimated,"rowing_minutes":estimated,"quality_minutes":estimated,"band":"RACE","structure":"Race day; no ordinary training.","race_distance":race.get("race_type"),"race_priority":race.get("priority"),"expected_starts":starts,"warmup_guidance":"Use the athlete's practiced race warm-up.","cooldown_guidance":"Easy movement and recovery between starts.","warning":None}); day+=timedelta(days=1); continue
        today_activities=commitments.get(day.isoformat(),[]) if modern_schedule else []
        rest=next((item for item in today_activities if item.get("activity_type")=="rest"),None)
        strength=next((item for item in today_activities if item.get("activity_type")=="strength"),None)
        coached=next((item for item in today_activities if item.get("activity_type") in ("private_coaching","coached_row")),None)
        if rest or (not modern_schedule and (not a.get("available",False) or a.get("fixed_rest",False))):
            if not modern_schedule and a.get("heavy_lifting"): sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Fixed strength session."})
            day+=timedelta(days=1); continue
        if modern_schedule and strength:
            sessions.append(_commitment("strength",day,phase,a,strength))
            rules=strength.get("same_day_rules",{})
            if rules.get("alternate_ut2_allowed"):
                sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":False,"mode":"treadmill_walk_jog","session_id":"XL-UT2-01","title":"Post-lifting alternate UT2","total_cardio_minutes":min(35,a.get("max_training_minutes",60)),"rowing_minutes":0,"quality_minutes":0,"band":"UT2","structure":"Continuous easy-to-steady cross-training."})
            if not rules.get("rowing_allowed",True): day+=timedelta(days=1); continue
        if modern_schedule and coached:
            sessions.append(_commitment(coached.get("activity_type"),day,phase,a,coached)); day+=timedelta(days=1); continue
        if not modern_schedule and not a.get("available",False):
            if a.get("heavy_lifting"): sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Fixed strength session."})
            day+=timedelta(days=1); continue
        if not modern_schedule and a.get("heavy_lifting"):
            sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Fixed strength session."})
            if a.get("alternate_ut2_allowed"):
                sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":False,"mode":a["alternate_ut2_modes"][0],"session_id":"XL-UT2-01","title":"Post-lifting alternate UT2","total_cardio_minutes":min(35,a["max_training_minutes"]-a.get("lifting_minutes",0)),"rowing_minutes":0,"quality_minutes":0,"band":"UT2","structure":"Continuous easy-to-steady cross-training."})
            day+=timedelta(days=1); continue
        if not modern_schedule and a.get("fixed_coached_row"):
            sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"on_water","session_id":"COACHED","title":"Fixed coached row","total_cardio_minutes":min(75,a["max_training_minutes"]),"rowing_minutes":min(75,a["max_training_minutes"]),"quality_minutes":0,"band":"UT2/UT1","structure":"Coach-led technique and aerobic work.","warning":"Coached intensity is athlete-provided."}); day+=timedelta(days=1); continue
        if day.isoformat() not in ordinary_row_dates:
            day+=timedelta(days=1); continue
        # Taper protection prioritizes easy technical work.
        band = "UT3" if phase in ("taper_sharpen","race_recovery") else ("TR" if day.weekday()==1 and phase in ("race_build","specific_preparation") else "UT2")
        if day.weekday()==3: band="UT2" # Thursday is intentionally optional/easy.
        role=day_roles.get(day.isoformat())
        made=_session(day,phase,a,library,band,power,key_race_type,profile.get("preferences",{}).get("workout_structure_preference","varied"),role=role,experience=profile.get("athlete",{}).get("experience_level","intermediate"),history=selection_history)
        if made:
            made=transform(made,phase=phase,race_priority=(next_race or {}).get("priority"))
            sessions.append(made)
            if made.get("session_fingerprint"): selection_history.append({**made["session_fingerprint"],"date":made["date"]})
        day+=timedelta(days=1)
    # Transform fixed strength after placement; ordinary rows were transformed
    # at instantiation. Locked/completed sessions are restored unchanged below.
    sessions=[transform(s,phase=s.get("phase",""),race_priority=(next((r for r in races if parse(r["start_date"])>=date.fromisoformat(s["date"])),{}) or {}).get("priority")) if s.get("session_id")=="LIFT" else s for s in sessions]
    # restore locked sessions by exact date, preserving byte-identical dictionaries
    lock_dates={s["date"] for s in locked_sessions or []}
    if lock_dates:
        sessions=[s for s in sessions if s["date"] not in lock_dates]+[s for s in locked_sessions if s["date"] in lock_dates]
    sessions.sort(key=lambda s:(s["date"],str(s.get("session_id"))))
    byweek=defaultdict(list)
    for s in sessions: byweek[date.fromisoformat(s["date"]).isocalendar().week].append(s)
    totals=[]
    for week,items in sorted(byweek.items()):
        hard=[s for s in items if s.get("band") in ("TR","AN","PP")]
        lifts=sum(s.get("session_id")=="LIFT" for s in items)
        if len(hard)>2: warnings.append({"level":"warning","message":f"Week {week} has more than two high-intensity rows."})
        if lifts>=3 and len(hard)>1: warnings.append({"level":"warning","message":f"Week {week} combines three heavy lifts with more than one hard row."})
        totals.append({"week":week,"cardio_minutes":sum(s.get("total_cardio_minutes",0) for s in items),"rowing_minutes":sum(s.get("rowing_minutes",0) for s in items),"strength_sessions":lifts,"quality_sessions":len(hard)})
    band_map={b["name"]:b for b in bands}
    for s in sessions:
        matching=[band_map[x] for x in s.get("band","").split("/") if x in band_map]
        lows=[b["hr_low"] for b in matching if b.get("hr_low") is not None]
        highs=[b["hr_high"] for b in matching if b.get("hr_high") is not None]
        rate_lows=[b["spm_low"] for b in matching if b.get("spm_low") is not None]
        rate_highs=[b["spm_high"] for b in matching if b.get("spm_high") is not None]
        s["hr_range"] = f"{min(lows)}–{max(highs)} bpm" if lows and highs else "Use breathing / RPE"
        s["coached"] = s.get("session_id") == "COACHED"
        s["rating"] = f"{min(rate_lows)}–{max(rate_highs)} spm" if rate_lows and rate_highs else "—"
        s["description"] = f"{s.get('title', '')}. {s.get('structure', '')}".strip()
    # Legacy profiles do not have editable recurring activities yet, but still
    # expose migration-ready scheduling metadata for a future profile edit.
    if not modern_schedule:
        quality_days={s["day"].lower() for s in sessions if s.get("band") in ("TR","AN","PP","RACE")}; fixed_days={s["day"].lower() for s in sessions if s.get("fixed")}
        schedule_moves=[{"activity_type":a.get("activity_type"),**choose(a,quality_days,fixed_days)} for a in migrate_legacy_availability(profile) if a.get("scheduling_status")!="fixed" and a.get("planner_may_choose_day",True)]
    calendar_days=_calendar_days(profile,start,end,commitments,modern_schedule); frequency_errors,frequency_exceptions=_validate_weekly_frequencies(profile,start,end,calendar_days,phases)
    if frequency_errors: raise ValueError(" ".join(frequency_errors))
    impacts=power.get("plan_impacts",[])
    volume_feasibility=_reconcile_low_intensity_volume(weekly_training_intents,sessions)
    return {"plan_version":"0.7.0","profile_id":profile.get("athlete",{}).get("display_name","athlete"),"generated_at":datetime.now().isoformat(),"schedule_signature":schedule_signature(profile),"intensity_profile":bands,"power_profile":power,"phases":phases,"season_phases":season_phases,"weekly_training_intents":weekly_training_intents,"calendar_days":calendar_days,"frequency_exceptions":frequency_exceptions,"weekly_volume_feasibility":volume_feasibility,"hard_session_spacing":_hard_session_spacing(sessions),"sessions":sessions,"weekly_totals":totals,"warnings":warnings+[{"level":"info","message":w} for w in power.get("warnings",[])],"plan_impacts":impacts,"schedule_moves":schedule_moves,"evidence_methodology":METHODOLOGY_STATEMENT,"evidence_rules":RULES,"algorithm_versions":{"planner":"0.7.0","phase_weekly_intent":PLANNING_MODEL_VERSION,"session_selection":SELECTION_VERSION,"archetype_catalog":"0.1.0","progression":"deterministic-piece-duration-0.1.0","load_transformation":TRANSFORMATION_VERSION,"taper_rule":"role-sensitive-taper-0.1.0","recovery_rule":"role-sensitive-recovery-0.1.0","power_profile":power.get("algorithm_version"),"config":config["config_version"]}}
