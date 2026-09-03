"""Step 3 archetype catalog.  It is intentionally not used by the scheduler.

Archetypes are parameter envelopes, not pre-written workouts.  Step 4 may
later instantiate one against weekly intent, duration and athlete history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

ARCHETYPE_LIBRARY_VERSION = "0.1.0"
ROWING_BANDS = {"UT3", "UT2", "UT1", "AT", "TR", "AN", "PP"}
EXPERIENCE = {"novice", "intermediate", "experienced", "competitive"}
ROLES = {"technique", "recovery", "aerobic_base", "aerobic_endurance", "threshold", "race_development", "anaerobic_capacity", "sprint_power", "head_race", "two_k", "one_k", "strength", "alternate_aerobic", "coached_technique", "coached_aerobic", "coached_quality", "private_coaching"}
INTENT_ROLE_ALIASES = {"LONG_AEROBIC":"aerobic_base", "AEROBIC_BASE":"aerobic_base", "AEROBIC_STRENGTH":"aerobic_endurance", "THRESHOLD":"threshold", "RACE_PACE":"race_development", "RECOVERY":"recovery", "TECHNIQUE_EASY":"technique"}

@dataclass(frozen=True)
class SessionArchetype:
    archetype_id: str; name: str; session_role: str; primary_band: str | None
    secondary_bands: list[str]; training_objectives: list[str]; experience_levels: list[str]
    phase_fit: list[str]; race_fit: list[str]; environment: list[str]; structure_family: str
    duration_range_min: dict[str, int]; work_interval_range_min: dict[str, int]
    repetition_range: dict[str, int]; recovery_range_min: dict[str, int]
    rate_range_spm: dict[str, int]; preference_fit: dict[str, str]
    novice_allowed: bool; taper_compatible: bool; progression_dimensions: list[str]
    load_classification: str; requires_easy_day_before: bool; requires_easy_day_after: bool
    minimum_recovery_guidance: str; same_day_strength_compatibility: str
    source_ids: list[str]; rule_origin: str; original_app_wording: bool; notes: str
    def to_dict(self) -> dict: return asdict(self)

PREF = {"shorter_pieces": "acceptable", "gradual_build": "good", "longer_pieces": "acceptable", "mixed": "good"}
RANGES = {
 "technical_intervals": (25,50,3,6,3,8,1,2,16,20), "drill_aerobic": (25,55,4,10,2,5,1,3,16,20), "easy_continuous": (20,60,15,45,1,1,0,0,16,20), "broken_recovery": (20,45,3,7,3,6,1,3,16,18), "low_rate_rhythm": (30,55,6,12,2,5,1,3,16,19),
 "short_repeats": (35,60,5,10,4,8,1,3,18,22), "medium_repeats": (40,75,10,15,3,6,1,4,18,22), "long_repeats": (45,90,15,30,2,4,2,5,18,22), "continuous": (35,90,25,70,1,1,0,0,18,22), "progressive_duration": (40,80,6,20,3,5,1,4,18,23), "rate_controlled": (40,75,8,18,3,5,1,3,18,22), "technique_aerobic": (35,70,6,15,3,5,1,3,17,21),
 "threshold_short": (35,60,5,6,4,6,2,4,22,26), "threshold_medium": (40,70,7,8,3,5,2,4,22,27), "threshold_long": (45,75,10,14,2,4,2,5,22,28), "distance_threshold": (40,70,6,12,3,5,2,4,22,28), "progressive_threshold": (40,70,6,12,3,5,2,4,22,28),
 "race_intervals": (35,60,3,5,4,7,3,6,26,34), "race_rate_blocks": (35,60,3,8,3,6,2,5,26,36), "distance_repeats": (35,65,2,5,4,7,3,6,26,36), "mixed_rate": (35,60,4,8,3,5,2,5,24,36), "rate_change": (35,60,3,6,4,7,3,6,24,36),
 "thirty_second": (25,45,1,1,6,12,2,5,30,40), "fortyfive_second": (30,50,1,1,5,10,3,6,30,40), "sixty_second": (30,55,1,1,4,8,3,7,30,40), "ninety_second": (35,60,1,2,3,6,4,8,30,40), "short_distance": (30,55,1,2,4,8,3,7,30,40),
 "stroke_power": (20,40,1,1,6,15,2,5,34,44), "starts": (20,40,1,1,6,12,2,5,34,44), "accelerations": (20,40,1,1,6,12,2,5,32,42), "start_settle": (25,45,1,2,5,10,3,6,32,42),
 "coached": (30,90,0,0,1,1,0,0,16,38), "strength": (35,70,0,0,1,1,0,0,0,0), "alternate": (20,60,10,50,1,1,0,0,0,0),
}

def _pref(family: str) -> dict[str, str]:
    result = dict(PREF)
    if family == "continuous": result.update({"shorter_pieces":"poor_fit", "longer_pieces":"preferred"})
    if family in {"short_repeats","thirty_second","fortyfive_second","sixty_second","stroke_power","starts"}: result.update({"shorter_pieces":"preferred", "longer_pieces":"poor_fit"})
    if family in {"progressive_duration","progressive_threshold"}: result["gradual_build"]="preferred"
    return result

def _make(identifier: str, name: str, role: str, band: str | None, family: str, *, exp: list[str] | None=None, phases: list[str] | None=None, races: list[str] | None=None, env: list[str] | None=None, novice=False, load="low", notes="") -> dict:
    a,b,c,d,e,f,g,h,i,j = RANGES[family]
    high = load in {"high","very_high"}
    return SessionArchetype(identifier,name,role,band,[],[role.replace("_"," "),"technical consistency"],exp or ["intermediate","experienced","competitive"],phases or ["aerobic_development","general_preparation"],races or ["general"],env or ["erg","water"],family,{"min":a,"max":b},{"min":c,"max":d},{"min":e,"max":f},{"min":g,"max":h},{"min":i,"max":j},_pref(family),novice,not high,["piece_duration","total_work","recovery","rate"],load,high,high,"48 hours or an easy day" if high else "Use athlete response and next-day movement quality", "avoid_heavy_strength" if high else "compatible_when_athlete_approved", ["S010","S017"] if not high else ["S014","S015","S016"],"research_informed_app_rule",True,notes or "Original app wording; parameters are app-defined coaching ranges, not copied protocols.").to_dict()

def _rows(prefix: str, role: str, band: str, families: list[tuple[str,str]], **kw) -> list[dict]:
    return [_make(f"{prefix}_{n:02d}", name, role, band, family, **kw) for n,(name,family) in enumerate(families,1)]

def build_archetype_library() -> list[dict]:
    """Return the versioned original archetype catalog; no scheduler calls this."""
    items=[]
    items += _rows("ut3","technique","UT3",[("Technical interval reset","technical_intervals"),("Drill and easy connection","drill_aerobic"),("Easy continuous movement","easy_continuous"),("Broken recovery rhythm","broken_recovery"),("Low-rate technical rhythm","low_rate_rhythm")], exp=["novice","intermediate","experienced","competitive"], novice=True, phases=["foundation_orientation","transition","post_race_recovery","general_preparation"])
    items += _rows("ut2","aerobic_base","UT2",[("Short aerobic repeats","short_repeats"),("Medium aerobic repeats","medium_repeats"),("Long aerobic repeats","long_repeats"),("Continuous aerobic row","continuous"),("Progressive-duration aerobic row","progressive_duration"),("Rate-controlled aerobic work","rate_controlled"),("Technique plus aerobic blocks","technique_aerobic"),("Aerobic pace-change row","progressive_duration")])
    items += _rows("ut1","aerobic_endurance","UT1",[("Controlled shorter endurance repeats","short_repeats"),("Medium endurance repeats","medium_repeats"),("Long sustained endurance blocks","long_repeats"),("Progressive endurance pieces","progressive_duration"),("Rate-capped aerobic strength","rate_controlled"),("UT2-to-UT1 bridge","technique_aerobic")], load="moderate", phases=["aerobic_development","general_preparation","threshold_development","race_specific_preparation"])
    items += _rows("at","threshold","AT",[("Short threshold repeats","threshold_short"),("Medium threshold repeats","threshold_medium"),("Long threshold blocks","threshold_long"),("Distance-based threshold work","distance_threshold"),("Progressive threshold work","progressive_threshold"),("Threshold rate-control blocks","threshold_medium")], load="high", phases=["threshold_development","race_specific_preparation"])
    items += _rows("tr","race_development","TR",[("Race-development intervals","race_intervals"),("Race-rate blocks","race_rate_blocks"),("Distance race repeats","distance_repeats"),("Mixed-rate pieces","mixed_rate"),("Rate-change development","rate_change"),("Controlled high-rate work","race_intervals")], load="high", phases=["race_specific_preparation","taper"])
    items += _rows("an","anaerobic_capacity","AN",[("Thirty-second power repetitions","thirty_second"),("Forty-five-second power repetitions","fortyfive_second"),("Sixty-second repeat power","sixty_second"),("Ninety-second capacity pieces","ninety_second"),("Short-distance power repeats","short_distance")], exp=["experienced","competitive"], load="very_high", phases=["race_specific_preparation"])
    items += _rows("pp","sprint_power","PP",[("Stroke-count maximal work","stroke_power"),("Start practice","starts"),("Short acceleration work","accelerations"),("Start-and-settle sequence","start_settle"),("Very-short power clusters","stroke_power")], exp=["experienced","competitive"], load="very_high", phases=["race_specific_preparation","taper"])
    for prefix,role,race,band,families in [
      ("head","head_race","head_5k","TR",[("Head aerobic-strength rhythm","long_repeats"),("Head threshold transition","threshold_medium"),("Head race-rate blocks","race_rate_blocks"),("Head rate transitions","rate_change"),("Head start and settle","start_settle"),("Head controlled simulation","mixed_rate")]),
      ("two_k","two_k","erg_2k","TR",[("Two-k sustained threshold","threshold_long"),("Two-k rate development","race_rate_blocks"),("Two-k 500-to-1000 pieces","distance_repeats"),("Two-k pacing practice","mixed_rate"),("Two-k start and settle","start_settle"),("Two-k partial simulation","race_intervals")]),
      ("one_k","one_k","sprint_1k","AN",[("One-k start development","starts"),("One-k high-rate technique","rate_change"),("One-k short race pieces","short_distance"),("One-k anaerobic capacity","sixty_second"),("One-k sprint-power maintenance","stroke_power"),("One-k broken-race finish","ninety_second")])]:
        items += _rows(prefix,role,band,families, exp=["experienced","competitive"], races=[race], phases=["race_specific_preparation","taper"], load="very_high" if race=="sprint_1k" else "high")
    items += _rows("novice","aerobic_base","UT2",[("Technique with very short easy rowing","technical_intervals"),("Short manageable aerobic repeats","short_repeats"),("Manageable continuous rowing","continuous"),("Technique between aerobic blocks","technique_aerobic"),("Low-rate rhythm practice","low_rate_rhythm"),("Progressive-duration novice row","progressive_duration"),("Easy repeat confidence row","short_repeats"),("Short aerobic consolidation","medium_repeats")], exp=["novice"], novice=True, phases=["foundation_orientation","aerobic_development","general_preparation"])
    items += [_make("strength_heavy","Heavy strength","strength",None,"strength",env=["strength"],load="high",notes="Role-only strength commitment; coach or qualified strength professional directs content."),_make("strength_maintenance","Strength maintenance","strength",None,"strength",env=["strength"],load="moderate"),_make("strength_taper","Reduced-load taper strength","strength",None,"strength",env=["strength"],load="low")]
    items += [_make(f"alternate_{m}",f"Alternate UT2: {m.title()}","alternate_aerobic",None,"alternate",env=[m],notes="Cardiovascular UT2 only; never count as rowing-zone minutes.") for m in ["treadmill","elliptical","bike","other_aerobic"]]
    items += [_make("coached_technique","Coached technique","coached_technique","UT3","coached",env=["water","erg"],notes="Coach instructions take priority."),_make("coached_aerobic","Coached aerobic row","coached_aerobic","UT2","coached",env=["water","erg"],notes="Coach instructions take priority."),_make("coached_quality","Coached quality row","coached_quality","TR","coached",env=["water","erg"],load="high",notes="Coach instructions take priority."),_make("private_coaching","Private coaching","private_coaching",None,"coached",env=["water","erg"],notes="Coach instructions take priority.")]
    return items

class SessionArchetypeValidator:
    def validate(self, archetypes: Iterable[dict]) -> list[str]:
        errors=[]
        for item in archetypes:
            ident=item.get("archetype_id","<missing>")
            if item.get("primary_band") and item["primary_band"] not in ROWING_BANDS: errors.append(f"{ident}: unknown primary band")
            if item.get("session_role") not in ROLES: errors.append(f"{ident}: unknown session role")
            if not set(item.get("experience_levels",[])) <= EXPERIENCE: errors.append(f"{ident}: invalid experience eligibility")
            for key in ("duration_range_min","work_interval_range_min","repetition_range","recovery_range_min","rate_range_spm"):
                value=item.get(key,{})
                if value.get("min") is None or value.get("max") is None or value["min"]>value["max"]: errors.append(f"{ident}: invalid {key}")
            if item.get("load_classification") in {"high","very_high"} and not item.get("minimum_recovery_guidance"): errors.append(f"{ident}: missing recovery guidance")
            if item.get("novice_allowed") and item.get("primary_band") in {"AT","TR","AN","PP"}: errors.append(f"{ident}: novice-inappropriate band")
            if item.get("session_role") in {"head_race","two_k","one_k"} and item.get("race_fit")==["general"]: errors.append(f"{ident}: race role lacks race type")
            if item.get("session_role") in {"strength","alternate_aerobic"} and item.get("primary_band"): errors.append(f"{ident}: non-rowing archetype has rowing band")
            if not item.get("source_ids") or not item.get("rule_origin") or not item.get("original_app_wording"): errors.append(f"{ident}: missing provenance or original wording flag")
        return errors

def eligible_archetypes(*, experience: str, role: str | None=None, race_type: str | None=None, environment: str | None=None, band: str | None=None) -> list[dict]:
    """Developer/query helper only; this does not rank or select a workout."""
    result=[]
    role = INTENT_ROLE_ALIASES.get(role, role)
    for item in build_archetype_library():
        if experience not in item["experience_levels"]: continue
        if role and item["session_role"] != role: continue
        if race_type and race_type not in item["race_fit"]: continue
        if environment and environment not in item["environment"]: continue
        if band and item["primary_band"] != band: continue
        result.append(item)
    return result

def developer_report() -> str:
    """Compact technical browser content, intentionally not exposed to athletes."""
    lines=[f"# Session archetypes ({ARCHETYPE_LIBRARY_VERSION})", "", "| ID | Name | Band | Role | Structure | Work range (min) | Rate (spm) | Experience | Phase fit | Race | Environment | Load | Sources |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in sorted(build_archetype_library(), key=lambda x:(str(x["primary_band"]),x["archetype_id"])):
        work=item["work_interval_range_min"]
        rate=item["rate_range_spm"]
        lines.append(f"| {item['archetype_id']} | {item['name']} | {item['primary_band'] or 'non-rowing'} | {item['session_role']} | {item['structure_family']} | {work['min']}–{work['max']} | {rate['min']}–{rate['max']} | {', '.join(item['experience_levels'])} | {', '.join(item['phase_fit'])} | {', '.join(item['race_fit'])} | {', '.join(item['environment'])} | {item['load_classification']} | {', '.join(item['source_ids'])} |")
    return "\n".join(lines)
