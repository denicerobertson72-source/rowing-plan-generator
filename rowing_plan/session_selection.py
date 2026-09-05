"""Deterministic role-aware archetype selection and concrete instantiation."""
from __future__ import annotations
from .session_archetypes import build_archetype_library

VERSION="archetype-selection-0.2.0"
ROLE_BAND={"TECHNIQUE_EASY":"UT3","RECOVERY":"UT3","AEROBIC_BASE":"UT2","LONG_AEROBIC":"UT2","AEROBIC_STRENGTH":"UT1","THRESHOLD":"AT","RACE_PACE":"TR","SPRINT_POWER":"PP"}
ROLE_MATCH={"TECHNIQUE_EASY":{"technique"},"RECOVERY":{"recovery","technique"},"AEROBIC_BASE":{"aerobic_base"},"LONG_AEROBIC":{"aerobic_base"},"AEROBIC_STRENGTH":{"aerobic_endurance"},"THRESHOLD":{"threshold"},"RACE_PACE":{"race_development","head_race","two_k","one_k"},"SPRINT_POWER":{"sprint_power","one_k"}}
FAMILY={
 "AEROBIC_BASE":{"medium_repeats":30,"progressive_duration":28,"rate_controlled":25,"technique_aerobic":22,"continuous":20,"long_repeats":18,"short_repeats":12},
 "LONG_AEROBIC":{"continuous":36,"long_repeats":33,"progressive_duration":30,"medium_repeats":14,"rate_controlled":12,"technique_aerobic":8,"short_repeats":-24},
 "TECHNIQUE_EASY":{"technical_intervals":34,"drill_aerobic":32,"low_rate_rhythm":30,"easy_continuous":24,"broken_recovery":20},
 "RECOVERY":{"broken_recovery":30,"easy_continuous":26,"drill_aerobic":22,"technical_intervals":18,"low_rate_rhythm":16},
 "AEROBIC_STRENGTH":{"long_repeats":30,"rate_controlled":28,"progressive_duration":25,"medium_repeats":20,"technique_aerobic":14,"short_repeats":8},
 "THRESHOLD":{"threshold_long":34,"progressive_threshold":31,"threshold_medium":27,"distance_threshold":24,"threshold_short":12},
 "RACE_PACE":{"race_rate_blocks":34,"mixed_rate":31,"rate_change":28,"distance_repeats":26,"race_intervals":20,"start_settle":18},
 "SPRINT_POWER":{"stroke_power":30,"starts":28,"accelerations":26,"start_settle":22},
}
WEIGHTS={"phase":12,"race":10,"preference":5,"history":9,"duplicate":12,"progression":8,"duration":8}

def assign_week_roles(dates,intent,race_type):
    """Assign distinct purposes; fixed coaching stays outside this mapping."""
    types={x["phase_type"] for x in intent.get("phase_mix",[])}; count=len(dates)
    if "taper" in types: base=["RACE_PACE","TECHNIQUE_EASY","LONG_AEROBIC","AEROBIC_BASE"]
    elif types & {"race_specific_preparation"}: base=["RACE_PACE","THRESHOLD","LONG_AEROBIC","TECHNIQUE_EASY","AEROBIC_BASE"]
    elif intent.get("load_direction") in {"recover","recover_then_build"}: base=["RECOVERY","TECHNIQUE_EASY","AEROBIC_BASE","LONG_AEROBIC"]
    elif types & {"threshold_development"}: base=["THRESHOLD","LONG_AEROBIC"]
    elif count==2: base=["AEROBIC_STRENGTH","LONG_AEROBIC"]
    else: base=["AEROBIC_BASE","TECHNIQUE_EASY","LONG_AEROBIC","AEROBIC_STRENGTH","AEROBIC_BASE"]
    return {day:base[min(index,len(base)-1)] for index,day in enumerate(sorted(dates))}

def _environment(mode): return "water" if mode=="on_water" else "erg"
def _pref(value): return {"short_intervals":"shorter_pieces","long_intervals":"longer_pieces","varied":"mixed","repeatable":"mixed"}.get(value,value)
def _phase(value): return {"specific_preparation":"general_preparation","race_build":"race_specific_preparation","taper_sharpen":"taper","race_recovery":"post_race_recovery"}.get(value,value)

def candidates(*,role,band,experience,phase,race_type,mode,minutes):
    env,phase,result=_environment(mode),_phase(phase),[]
    for item in build_archetype_library():
        if item["primary_band"]!=band or item["session_role"] not in ROLE_MATCH.get(role,set()): continue
        if experience not in item["experience_levels"] or env not in item["environment"]: continue
        if phase not in item["phase_fit"] and "general_preparation" not in item["phase_fit"] and not (phase=="taper" and item.get("taper_compatible")): continue
        if race_type!="general" and item["session_role"] in {"head_race","two_k","one_k"} and race_type not in item["race_fit"]: continue
        if item["duration_range_min"]["min"]<=minutes: result.append(item)
    if role=="RACE_PACE":
        targeted=[item for item in result if race_type in item["race_fit"]]
        if targeted: result=targeted
    if role=="LONG_AEROBIC":
        sustained=[item for item in result if FAMILY[role].get(item["structure_family"],-99)>=20]
        if sustained: result=sustained
    return result

def _score(item,role,phase,race_type,preference,minutes,history):
    family=item["structure_family"]; role_fit=FAMILY.get(role,{}).get(family,0); phase_fit=WEIGHTS["phase"] if _phase(phase) in item["phase_fit"] else 4; race_fit=WEIGHTS["race"] if race_type in item["race_fit"] else 0
    preference_effect=item["preference_fit"].get(_pref(preference),"acceptable"); pref=WEIGHTS["preference"]*{"preferred":2,"good":1,"acceptable":0,"poor_fit":-2}.get(preference_effect,0)
    duration=round(WEIGHTS["duration"]*min(1,max(0,(minutes-item["duration_range_min"]["min"])/max(1,item["duration_range_min"]["max"]-item["duration_range_min"]["min"]))))
    recent=history[-8:]; same_id=any(x.get("archetype_id")==item["archetype_id"] and not x.get("benchmark_repeat") for x in recent); family_count=sum(x.get("structure_family")==family for x in recent)
    history_effect=-WEIGHTS["history"] if same_id else -min(WEIGHTS["history"],family_count*3); duplicate=-WEIGHTS["duplicate"] if any(x.get("structure_family")==family and x.get("primary_band")==item["primary_band"] for x in history[-3:]) else 0
    comparable=[x for x in history if x.get("session_role")==role]; progression=WEIGHTS["progression"] if comparable and family!=comparable[-1].get("structure_family") else 0
    components={"role_fit":role_fit,"phase_fit":phase_fit,"race_fit":race_fit,"preference":pref,"duration_fit":duration,"history":history_effect,"duplicate_structure":duplicate,"progression":progression}
    return sum(components.values()),components,preference_effect

def _instantiate(item,role,experience,minutes,history):
    work,reps,recovery=item["work_interval_range_min"],item["repetition_range"],item["recovery_range_min"]; available=max(1,minutes-12)
    capacity={"novice":.42,"intermediate":.58,"experienced":.66,"competitive":.72}.get(experience,.55); role_load={"LONG_AEROBIC":.78,"AEROBIC_BASE":.58,"AEROBIC_STRENGTH":.62,"THRESHOLD":.58,"RACE_PACE":.48,"TECHNIQUE_EASY":.42,"RECOVERY":.35,"SPRINT_POWER":.35}.get(role,.5); family=item["structure_family"]
    if reps["max"]==1: count=1
    elif family in {"long_repeats","threshold_long"}: count=min(reps["max"],max(reps["min"],2 if available>=45 else reps["min"]))
    elif family in {"medium_repeats","threshold_medium","progressive_duration","progressive_threshold"}: count=min(reps["max"],max(reps["min"],3))
    else: count=min(reps["max"],max(reps["min"],4))
    piece=round(available*max(capacity,role_load)/count)
    if role=="LONG_AEROBIC" and family!="continuous": piece=max(piece,15)
    comparable=[x for x in history if x.get("session_role")==role and x.get("structure_family")==family]
    if comparable: piece+=min(2,len(comparable))
    piece=max(work["min"],min(work["max"],piece))
    while count>reps["min"] and 12+count*piece+(count-1)*recovery["min"]>minutes: count-=1
    while piece>work["min"] and 12+count*piece+(count-1)*recovery["min"]>minutes: piece-=1
    total=min(minutes,12+count*piece+max(0,count-1)*recovery["min"])
    constrained=role=="LONG_AEROBIC" and family not in {"continuous","long_repeats","progressive_duration"}
    return piece,count,recovery["min"],total,constrained

def select_and_instantiate(*,role,experience,phase,race_type,mode,minutes,preference,history):
    band=ROLE_BAND[role]; pool=candidates(role=role,band=band,experience=experience,phase=phase,race_type=race_type,mode=mode,minutes=minutes)
    if not pool: return None
    scored=[]
    for item in pool:
        score,components,effect=_score(item,role,phase,race_type,preference,minutes,history); scored.append((score,item,components,effect))
    score,item,components,effect=max(scored,key=lambda row:(row[0],row[1]["archetype_id"])); piece,count,recovery,total,constrained=_instantiate(item,role,experience,minutes,history)
    comparable=[x for x in history if x.get("session_role")==role]; progression="piece_duration" if comparable else "initial_exposure"
    why=f"{role.replace('_',' ').title()} serves this week's intent through {item['structure_family'].replace('_',' ')}."
    if constrained: why+=" Available time constrained the sustained aerobic structure."
    if effect=="poor_fit": why+=" Structure preference is soft; training purpose takes priority."
    fingerprint={"archetype_id":item["archetype_id"],"session_role":role,"primary_band":band,"structure_family":item["structure_family"],"work_interval_duration":piece,"repetitions":count,"total_work_duration":piece*count,"recovery_duration":recovery,"rate_range":item["rate_range_spm"],"race_specificity":race_type in item["race_fit"]}
    return {"archetype":item,"candidate_scores":[{"archetype_id":candidate["archetype_id"],"score":candidate_score,"components":candidate_components} for candidate_score,candidate,candidate_components,_ in sorted(scored,key=lambda row:(-row[0],row[1]["archetype_id"]))],"work_interval_duration":piece,"repetitions":count,"recovery_duration":recovery,"total_minutes":total,"progression_dimension":progression,"preference_effect":effect,"selection_reason":why,"long_aerobic_constraint":"available_time_or_catalog" if constrained else None,"fingerprint":fingerprint}
