"""Constraint-first deterministic season scheduler."""
from __future__ import annotations
from datetime import date, timedelta, datetime
from collections import defaultdict
from .periodization import phase_for_day, parse, build_phases
from .session_selector import load_library, select_session
from .power_profile import target_for_band
from .evidence import METHODOLOGY_STATEMENT, RULES
from .conversions import watts_to_split_seconds, format_split

WEEKDAY=["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
def _availability(profile): return {x["weekday"]:x for x in profile["weekly_availability"]}
def _race(day,races): return next((r for r in races if parse(r["start_date"])<=day<=parse(r["end_date"])),None)
def _session(day, phase, avail, library, band, power, race_type, fixed=False, title=None):
    mode=next((m for m in avail.get("rowing_modes",[]) if m in ("on_water","erg")),"erg")
    minutes=min(avail.get("max_training_minutes",60), 60 if band in ("UT2","UT1") else 50)
    template=select_session(library,band,phase,race_type,[mode],minutes) or select_session(library,band,"all",race_type,[mode],minutes)
    if not template: return None
    anchor=target_for_band(power,band) if mode=="erg" else None
    watts = round((anchor["target_watts_low"]+anchor["target_watts_high"])/2,1) if anchor else None
    return {"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":fixed,"mode":mode,"session_id":template["session_id"],"title":title or template["title"],"total_cardio_minutes":minutes,"rowing_minutes":minutes,"quality_minutes":minutes if band in ("AT","TR","AN","PP") else 0,"band":band,"structure":template["work_structure"],"recovery":template["recovery_structure"],"technical_cue":template["technical_cues"][0],"rate_guide":template.get("spm_guidance"),"source_basis_ids":template["source_basis_ids"],"power_target_method":anchor["formula"] if anchor else "Intensity provider / HRR-RPE guidance", "source_anchor":anchor["source_test"] if anchor else None,"target_watts":watts,"split_guide":format_split(watts_to_split_seconds(watts)) if watts else None,"confidence":anchor["confidence"] if anchor else "low","assumptions":anchor["assumptions"] if anchor else ["Follow rate, breathing, and RPE where exact power is unavailable."]}

def generate_plan(profile: dict, config: dict, bands: list[dict], power: dict, locked_sessions: list[dict] | None = None) -> dict:
    library=load_library(); avails=_availability(profile); races=profile.get("races",[]); locked={(s["date"],s.get("session_id")):s for s in (locked_sessions or [])}; sessions=[]; warnings=[]
    start,end=parse(profile["season"]["start_date"]),parse(profile["season"]["end_date"]); day=start
    while day<=end:
        phase,next_race=phase_for_day(day,races); a=avails.get(WEEKDAY[day.weekday()],{}); race=_race(day,races); key_race_type=(next_race or races[0] if races else {}).get("race_type","head_5k")
        if race:
            sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":"race","fixed":True,"mode":"race","session_id":"RACE","title":race["event_name"],"total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"RACE","structure":"Race day; no ordinary training.","warning":None}); day+=timedelta(days=1); continue
        if not a.get("available",False) or a.get("fixed_rest",False):
            if a.get("heavy_lifting"): sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Fixed strength session."})
            day+=timedelta(days=1); continue
        if a.get("heavy_lifting"):
            sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Fixed strength session."})
            if a.get("alternate_ut2_allowed"):
                sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":False,"mode":a["alternate_ut2_modes"][0],"session_id":"XL-UT2-01","title":"Post-lifting alternate UT2","total_cardio_minutes":min(35,a["max_training_minutes"]-a.get("lifting_minutes",0)),"rowing_minutes":0,"quality_minutes":0,"band":"UT2","structure":"Continuous easy-to-steady cross-training."})
            day+=timedelta(days=1); continue
        if a.get("fixed_coached_row"):
            sessions.append({"date":day.isoformat(),"day":day.strftime("%A"),"phase":phase,"fixed":True,"mode":"on_water","session_id":"COACHED","title":"Fixed coached row","total_cardio_minutes":min(75,a["max_training_minutes"]),"rowing_minutes":min(75,a["max_training_minutes"]),"quality_minutes":0,"band":"UT2/UT1","structure":"Coach-led technique and aerobic work.","warning":"Coached intensity is athlete-provided."}); day+=timedelta(days=1); continue
        # Taper protection prioritizes easy technical work.
        band = "UT3" if phase in ("taper_sharpen","race_recovery") else ("TR" if day.weekday()==1 and phase in ("race_build","specific_preparation") else "UT2")
        if day.weekday()==3: band="UT2" # Thursday is intentionally optional/easy.
        made=_session(day,phase,a,library,band,power,key_race_type)
        if made: sessions.append(made)
        day+=timedelta(days=1)
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
    impacts=power.get("plan_impacts",[])
    return {"plan_version":"0.5.0","profile_id":profile.get("athlete",{}).get("display_name","athlete"),"generated_at":datetime.now().isoformat(),"intensity_profile":bands,"power_profile":power,"phases":build_phases(profile),"sessions":sessions,"weekly_totals":totals,"warnings":warnings+[{"level":"info","message":w} for w in power.get("warnings",[])],"plan_impacts":impacts,"evidence_methodology":METHODOLOGY_STATEMENT,"evidence_rules":RULES,"algorithm_versions":{"planner":"0.5.0","power_profile":power.get("algorithm_version"),"config":config["config_version"]}}
