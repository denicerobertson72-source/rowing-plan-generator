"""Deterministic Step 4 archetype filtering, scoring and instantiation."""
from __future__ import annotations
from .session_archetypes import build_archetype_library

VERSION="archetype-selection-0.1.0"
ROLE_BAND={"TECHNIQUE_EASY":"UT3","RECOVERY":"UT3","AEROBIC_BASE":"UT2","LONG_AEROBIC":"UT2","AEROBIC_STRENGTH":"UT1","THRESHOLD":"AT","RACE_PACE":"TR","SPRINT_POWER":"PP"}
ROLE_MATCH={"TECHNIQUE_EASY":{"technique"},"RECOVERY":{"recovery","technique"},"AEROBIC_BASE":{"aerobic_base"},"LONG_AEROBIC":{"aerobic_base"},"AEROBIC_STRENGTH":{"aerobic_endurance"},"THRESHOLD":{"threshold"},"RACE_PACE":{"race_development","head_race","two_k","one_k"},"SPRINT_POWER":{"sprint_power","one_k"}}
WEIGHTS={"role":40,"phase":12,"race":10,"preference":5,"variety":9,"progression":4,"duration":6}

def assign_week_roles(dates, intent, race_type):
    """Assign roles before selection; fixed coaching is handled by scheduler."""
    phase_types={x["phase_type"] for x in intent.get("phase_mix",[])}
    if "taper" in phase_types:
        base=["RACE_PACE","TECHNIQUE_EASY","LONG_AEROBIC","AEROBIC_BASE"]
    elif phase_types & {"race_specific_preparation"}:
        base=["RACE_PACE","THRESHOLD","LONG_AEROBIC","TECHNIQUE_EASY","AEROBIC_BASE"]
    elif intent.get("load_direction") in {"recover","recover_then_build"}:
        base=["RECOVERY","AEROBIC_BASE","LONG_AEROBIC","TECHNIQUE_EASY"]
    else:
        base=["AEROBIC_BASE","TECHNIQUE_EASY","LONG_AEROBIC","AEROBIC_STRENGTH","AEROBIC_BASE"]
    return {day:base[min(i,len(base)-1)] for i,day in enumerate(sorted(dates))}

def _environment(mode): return "water" if mode=="on_water" else "erg"
def _pref_name(value): return {"short_intervals":"shorter_pieces","long_intervals":"longer_pieces","varied":"mixed","repeatable":"mixed"}.get(value,value)

def candidates(*, role, band, experience, phase, race_type, mode, minutes):
    env=_environment(mode); result=[]
    phase={"specific_preparation":"general_preparation","race_build":"race_specific_preparation","taper_sharpen":"taper","race_recovery":"post_race_recovery"}.get(phase,phase)
    for item in build_archetype_library():
        if item["primary_band"] != band or item["session_role"] not in ROLE_MATCH.get(role,set()): continue
        if experience not in item["experience_levels"] or env not in item["environment"]: continue
        if phase not in item["phase_fit"] and "general_preparation" not in item["phase_fit"] and not (phase == "taper" and item.get("taper_compatible")): continue
        if race_type != "general" and item["session_role"] in {"head_race","two_k","one_k"} and race_type not in item["race_fit"]: continue
        if item["duration_range_min"]["min"] > minutes: continue
        result.append(item)
    # Race-specific roles prefer the distance-specific family but retain generic
    # TR when no distance-specific candidate can fit.
    if role=="RACE_PACE":
        targeted=[x for x in result if race_type in x["race_fit"]]
        if targeted: result=targeted
    return result

def select_and_instantiate(*, role, experience, phase, race_type, mode, minutes, preference, history):
    band=ROLE_BAND[role]; pool=candidates(role=role,band=band,experience=experience,phase=phase,race_type=race_type,mode=mode,minutes=minutes)
    if not pool: return None
    pref=_pref_name(preference); recent=history[-6:]
    scored=[]
    for item in pool:
        score=WEIGHTS["role"]+WEIGHTS["duration"]
        score += WEIGHTS["preference"]*{"preferred":2,"good":1,"acceptable":0,"poor_fit":-2}.get(item["preference_fit"].get(pref,"acceptable"),0)
        if any(x.get("archetype_id")==item["archetype_id"] and not x.get("benchmark_repeat") for x in recent): score-=WEIGHTS["variety"]
        if sum(x.get("structure_family")==item["structure_family"] for x in recent)>=2: score-=WEIGHTS["variety"]
        if race_type in item["race_fit"]: score+=WEIGHTS["race"]
        scored.append((score,item))
    score,item=max(scored,key=lambda row:(row[0],row[1]["archetype_id"]))
    work=item["work_interval_range_min"]; reps=item["repetition_range"]; recovery=item["recovery_range_min"]
    comparable=[x for x in history if x.get("session_role")==role]
    step=min(len(comparable), max(0,work["max"]-work["min"]))
    piece=work["min"]+step
    # Fit a complete warm-up/work/recovery/cool-down session deterministically.
    available=max(1,minutes-12); rep=max(reps["min"], min(reps["max"], available//max(1,piece+recovery["min"])))
    total=min(minutes,12+rep*piece+max(0,rep-1)*recovery["min"])
    progression="piece_duration" if comparable else "initial_exposure"
    why=f"{role.replace('_',' ').title()} serves this week's intent."
    if comparable: why+=f" Comparable prior work supports a small {progression.replace('_',' ')} progression."
    if item["preference_fit"].get(pref)=="poor_fit": why+= " Your structure preference is a soft preference; the training objective takes priority."
    return {"archetype":item,"candidate_scores":[{"archetype_id":x["archetype_id"],"score":s} for s,x in sorted(scored,key=lambda row:(-row[0],row[1]["archetype_id"]))],"work_interval_duration":piece,"repetitions":rep,"recovery_duration":recovery["min"],"total_minutes":total,"progression_dimension":progression,"preference_effect":item["preference_fit"].get(pref,"acceptable"),"selection_reason":why,"fingerprint":{"archetype_id":item["archetype_id"],"session_role":role,"primary_band":band,"structure_family":item["structure_family"],"work_interval_duration":piece,"repetitions":rep,"total_work_duration":piece*rep,"recovery_duration":recovery["min"],"rate_range":item["rate_range_spm"],"race_specificity":race_type in item["race_fit"]}}
