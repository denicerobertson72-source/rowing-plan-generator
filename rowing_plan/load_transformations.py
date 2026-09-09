"""Deterministic Step 5 post-instantiation load transformations."""
from __future__ import annotations
import re

VERSION="load-transformation-0.1.0"

def ensure_concrete_prescription(session: dict) -> dict:
    """Keep the displayed prescription compatible with transformed duration."""
    final_minutes=int(session.get("total_cardio_minutes",0))
    match=re.match(r"(\d+) × (\d+) min ([A-Z0-9/]+); (\d+) min easy recovery\.",session.get("structure", ""))
    if not match: return session
    repetitions,piece,band,recovery=(int(match.group(1)),int(match.group(2)),match.group(3),int(match.group(4)))
    overhead=int(session.get("modeled_overhead_minutes",12))
    while repetitions>1 and final_minutes-overhead-(repetitions-1)*recovery < repetitions*3:
        repetitions-=1
    piece=max(3,(final_minutes-overhead-(repetitions-1)*recovery)//repetitions)
    modeled=overhead+repetitions*piece+(repetitions-1)*recovery
    # Keep the PlanVersion internally exact: unused minutes are modeled as a
    # small cooldown rather than hiding a mismatch in a duration field.
    cooldown=max(0,final_minutes-modeled)
    structure=f"{repetitions} × {piece} min {band}; {recovery} min easy recovery."
    result={**session,"structure":structure,"modeled_overhead_minutes":overhead,"modeled_cooldown_minutes":cooldown}
    fingerprint=dict(result.get("session_fingerprint") or {})
    if fingerprint:
        fingerprint.update({"work_interval_duration":piece,"repetitions":repetitions,"total_work_duration":piece*repetitions,"recovery_duration":recovery,"modeled_overhead_minutes":overhead,"modeled_cooldown_minutes":cooldown})
        result["session_fingerprint"]=fingerprint
    return result

def transform(session: dict, *, phase: str, race_priority: str | None=None) -> dict:
    """Reduce quantity, never inflate intensity; retain provenance and reasons."""
    if session.get("session_id") == "RACE": return session
    if session.get("session_id") == "LIFT":
        if phase != "taper_sharpen": return session
        state="reduced-load" if race_priority=="A" else "maintenance" if race_priority=="B" else "heavy"
        return {**session,"title":f"{state.title()} strength","strength_state":state,"load_transformation":{"transformation_type":"strength_taper","original_archetype_id":None,"original_work_minutes":0,"final_work_minutes":0,"volume_factor":1,"frequency_preserved":True,"primary_band_preserved":True,"race_rate_preserved":False,"changed_parameters":["strength_fatigue_category"],"reason_codes":[f"{race_priority or 'A'}_RACE_STRENGTH"],"athlete_explanation":f"Strength is {state} to reduce race-week fatigue.","source_ids":["S017","S009"],"algorithm_version":VERSION}}
    if session.get("session_id") == "COACHED": return session
    role=session.get("session_role",""); band=session.get("band","")
    if phase not in {"taper_sharpen","race_recovery"}: return session
    high=band in {"AT","TR","AN","PP"}; factor=.72 if high else .62
    original=session.get("rowing_minutes",0); final=max(20,round(original*factor))
    record={"transformation_type":"taper" if phase=="taper_sharpen" else "post_race_recovery","original_archetype_id":session.get("archetype_id"),"original_work_minutes":original,"final_work_minutes":final,"volume_factor":round(final/original,2) if original else 1,"frequency_preserved":True,"primary_band_preserved":True,"race_rate_preserved":high,"changed_parameters":["total_work_duration","repetition_count"] if final<original else [],"reason_codes":[f"{race_priority or 'A'}_RACE_TAPER","REDUCE_ACCUMULATED_FATIGUE"]+(["RETAIN_RACE_SPECIFICITY"] if high else []),"athlete_explanation":"This session keeps its intended technical or race-specific focus while reducing accumulated fatigue.","source_ids":["S009","S010"],"algorithm_version":VERSION}
    transformed={**session,"original_structure":session.get("structure"),"total_cardio_minutes":final,"rowing_minutes":final,"quality_minutes":final if high else 0,"load_transformation":record,"transformation_reason":record["athlete_explanation"]}
    return ensure_concrete_prescription(transformed)
