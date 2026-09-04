"""Research-informed, athlete-specific multi-duration power profile.

This module uses actual 2k results as the integrated erg anchor.  It deliberately
does not predict a 2k from short tests or infer physiological thresholds.
"""
from __future__ import annotations
from datetime import date
from uuid import uuid4
from .conversions import two_k_seconds_to_watts

PROTOCOLS = ("two_k", "seven_stroke_peak", "sixty_second", "twenty_second_optional", "thirty_min_rate_capped_optional", "other")

def _date(value):
    try: return date.fromisoformat(value) if value else None
    except (TypeError, ValueError): return None
def _value(test: dict | None) -> float | None:
    if not test: return None
    for field in ("peak_watts", "average_watts", "value_watts"):
        if test.get(field) is not None: return float(test[field])
    if test.get("time_seconds"):
        return two_k_seconds_to_watts(float(test["time_seconds"]))
    return None
def profile_test_rejection_reason(test: dict | None, today: date, recency: int) -> str | None:
    """Explain why a test cannot be used as a current profile input.

    An athlete or coach can explicitly retain a result with
    ``valid_for_profile: true``. That explicit profile decision overrides the
    generic age default; unmarked legacy results continue to expire normally.
    """
    if not test:
        return "missing test"
    if test.get("valid_for_profile") is False or test.get("validity") not in (None, "valid"):
        return "marked invalid for profile"
    if not (_value(test) or test.get("time_seconds")):
        return "missing measurable result"
    test_date = _date(test.get("test_date"))
    if test_date and (today - test_date).days > recency and test.get("valid_for_profile") is not True:
        return f"older than the {recency}-day default without explicit profile approval"
    return None

def _valid(test, today, recency):
    return profile_test_rejection_reason(test, today, recency) is None

def _legacy_block(tests: dict) -> dict:
    raw=tests.get("multi_duration_power_tests") or {}
    items=[]
    if tests.get("erg_2k_seconds"):
        items.append({"id":"legacy-2k","protocol":"two_k","test_date":tests.get("test_date"),"time_seconds":tests["erg_2k_seconds"],"valid_for_profile":True,"source":"manual"})
    mapping=(
        ("short_peak","seven_stroke_peak","seven_stroke_peak_watts","peak_watts"),
        ("one_minute","sixty_second","sixty_second_avg_watts","average_watts"),
        ("rate_capped_sustained","thirty_min_rate_capped_optional","thirty_minute_r20_avg_watts","average_watts"),
    )
    for old_key,protocol,legacy_key,field in mapping:
        source=raw.get(old_key,{})
        value=source.get("value_watts",tests.get(legacy_key))
        if value:
            item={"id":f"legacy-{old_key}","protocol":protocol,"test_date":source.get("test_date",tests.get("test_date")),field:value,"valid_for_profile":source.get("validity","valid")=="valid","source":"manual","erg_model":source.get("erg_model"),"drag_factor":source.get("drag_factor"),"notes":source.get("notes","")}
            if protocol == "thirty_min_rate_capped_optional": item.update(duration_seconds=source.get("duration_seconds",1800),rate_cap_spm=source.get("rate_cap_spm",20))
            items.append(item)
    return {"id":raw.get("battery_id","legacy-current"),"label":"Current / migrated testing block","start_date":tests.get("test_date"),"end_date":tests.get("test_date"),"notes":"Migrated from v0.2 fields.","performance_tests":items}

def testing_blocks(profile: dict) -> list[dict]:
    """Return reusable testing blocks, migrating v0.2 data without data loss."""
    tests=profile.get("tests",{})
    blocks=tests.get("testing_blocks") or []
    if blocks: return blocks
    legacy=_legacy_block(tests)
    return [legacy] if legacy["performance_tests"] else []

def _tests_by_protocol(block: dict) -> dict[str, dict]:
    return {t.get("protocol"):t for t in block.get("performance_tests",[]) if t.get("protocol") in PROTOCOLS}

def _block_summary(block: dict, today: date, recency: int) -> dict:
    by=_tests_by_protocol(block); valid={p:t for p,t in by.items() if _valid(t,today,recency)}
    two,peak,sixty,twenty,r20=(valid.get("two_k"),valid.get("seven_stroke_peak"),valid.get("sixty_second"),valid.get("twenty_second_optional"),valid.get("thirty_min_rate_capped_optional"))
    watts={"two_k":_value(two),"seven_stroke_peak":_value(peak),"sixty_second":_value(sixty),"twenty_second":_value(twenty),"thirty_min_rate_capped":_value(r20)}
    ratios={"peak_to_2k_ratio": watts["seven_stroke_peak"]/watts["two_k"] if watts["seven_stroke_peak"] and watts["two_k"] else None,"sixty_to_2k_ratio": watts["sixty_second"]/watts["two_k"] if watts["sixty_second"] and watts["two_k"] else None,"sixty_to_peak_ratio": watts["sixty_second"]/watts["seven_stroke_peak"] if watts["sixty_second"] and watts["seven_stroke_peak"] else None,"twenty_to_2k_ratio": watts["twenty_second"]/watts["two_k"] if watts["twenty_second"] and watts["two_k"] else None,"r20_to_2k_ratio": watts["thirty_min_rate_capped"]/watts["two_k"] if watts["thirty_min_rate_capped"] and watts["two_k"] else None,"r20_to_60_ratio": watts["thirty_min_rate_capped"]/watts["sixty_second"] if watts["thirty_min_rate_capped"] and watts["sixty_second"] else None}
    if watts["two_k"] and watts["seven_stroke_peak"] and watts["sixty_second"]: status="2k_plus_peak_plus_60s"
    elif watts["two_k"] and watts["seven_stroke_peak"]: status="2k_plus_peak"
    elif watts["two_k"] and watts["sixty_second"]: status="2k_plus_60s"
    elif watts["two_k"]: status="2k_only"
    elif any((watts["seven_stroke_peak"],watts["sixty_second"])): status="short_tests_without_2k"
    else: status="unavailable"
    if watts["thirty_min_rate_capped"] and status != "unavailable": status="extended_profile" if status=="2k_plus_peak_plus_60s" else status
    return {"block":block,"tests":by,"watts":watts,"metrics":ratios,"status":status}

def _sort_key(block):
    return _date(block.get("end_date")) or _date(block.get("start_date")) or max((_date(t.get("test_date")) or date.min for t in block.get("performance_tests",[])),default=date.min)

def longitudinal(blocks: list[dict], today: date, recency: int) -> dict:
    summaries=sorted((_block_summary(b,today,recency) for b in blocks),key=lambda s:_sort_key(s["block"]))
    rows=[]
    for i,s in enumerate(summaries):
        current=s["watts"]; prior=summaries[i-1]["watts"] if i else {}
        ratios=s["metrics"]; prior_ratios=summaries[i-1]["metrics"] if i else {}
        two_test=s["tests"].get("two_k",{})
        row={"block_id":s["block"].get("id"),"label":s["block"].get("label","Testing block"),"date":_sort_key(s["block"]).isoformat() if _sort_key(s["block"]) != date.min else None,"two_k_time_seconds":two_test.get("time_seconds"),**current,**ratios}
        if i:
            for key in ("two_k","seven_stroke_peak","sixty_second","twenty_second","thirty_min_rate_capped"):
                if current.get(key) and prior.get(key): row[f"{key}_pct_change"]=(current[key]-prior[key])/prior[key]
            if row.get("two_k_time_seconds") and summaries[i-1]["tests"].get("two_k",{}).get("time_seconds"): row["two_k_time_change_seconds"]=row["two_k_time_seconds"]-summaries[i-1]["tests"]["two_k"]["time_seconds"]
            for key in ("peak_to_2k_ratio","sixty_to_2k_ratio","sixty_to_peak_ratio"):
                if ratios.get(key) is not None and prior_ratios.get(key) is not None: row[f"{key}_change"]=ratios[key]-prior_ratios[key]
        rows.append(row)
    return {"rows":rows,"current":summaries[-1] if summaries else None,"prior":summaries[-2] if len(summaries)>1 else None}

def _plan_impact(trends: dict) -> list[str]:
    current,prior=trends.get("current"),trends.get("prior")
    if not current: return ["No valid performance tests are available yet; use HR, RPE, rate, and coach guidance."]
    if not prior: return ["Current testing block establishes a baseline. Actual 2k is the primary erg performance anchor; short tests personalize PP and AN work."]
    row=trends["rows"][-1]; changes=[row.get(f"{x}_pct_change") for x in ("two_k","sixty_second")]
    available=[x for x in changes if x is not None]
    if available and all(x < 0 for x in available): return ["Recent 2k and available short-power values were lower. The app does not add intensity; review recovery, total load, illness, and test consistency before progression."]
    if row.get("two_k_pct_change",0)>0 and row.get("sixty_second_pct_change",0)<0:
        return ["2k power increased while at least one short-duration measure was lower. Keep the current aerobic progression and retain, rather than expand, a small short-power exposure when recovery and race timing allow."]
    if row.get("sixty_second_pct_change",0)>0 and row.get("two_k_pct_change",0)<=0:
        return ["Short-duration power increased while 2k power changed little or decreased. The next eligible development block may shift a small amount of quality work toward sustained UT1/AT or 2k-specific work; taper and recovery rules still win."]
    return ["Recent changes are modest or mixed. Continue the current progression and interpret test changes alongside normal variability and training context."]

def build_power_profile(profile: dict, config: dict, today: date | None = None) -> dict:
    today=today or date.today(); settings=config["power_profile"]; recency=settings["active_test_recency_days"]
    trends=longitudinal(testing_blocks(profile),today,recency); current=trends["current"]
    if not current:
        return {"algorithm_version":settings["algorithm_version"],"status":"unavailable","mode":"anchors_only","confidence":"unavailable","metrics":{},"anchors":[],"warnings":["No testing block available."],"assumptions":[],"testing_blocks":[],"longitudinal":trends,"plan_impacts":_plan_impact(trends)}
    watts=current["watts"]; tests=current["tests"]; warnings=[]
    for label,key in (("7-stroke", "seven_stroke_peak"),("60-second","sixty_second"),("2k","two_k")):
        test=tests.get(key)
        if test and not test.get("test_date"): warnings.append(f"{label} test has no date; confidence is reduced.")
    if watts["seven_stroke_peak"] and watts["sixty_second"] and watts["seven_stroke_peak"] <= watts["sixty_second"]: warnings.append("Peak power is not above 60-second power; automatic PP/AN anchors are suppressed pending confirmation.")
    anchors=[]; ordering_ok=not any("Peak power" in w for w in warnings)
    def anchor(name,source,percent,reason,ceiling=None):
        value=watts.get(source)
        if value and ordering_ok:
            low,high=value*percent[0],value*percent[1]
            anchors.append({"name":name,"source_test":source,"source_watts":value,"target_watts_low":low,"target_watts_high":min(high,ceiling) if ceiling else high,"ceiling_watts":ceiling,"formula":f"{percent[0]:.0%}–{percent[1]:.0%} of measured {source}","confidence":"medium" if tests.get(source,{}).get("test_date") else "low","assumptions":[reason,"Configurable app coaching rule; not a physiological threshold."]})
    anchor("two_k","two_k",settings["anchors"]["two_k_reference_pct_of_actual_2k"],"Actual 2k is the primary integrated erg performance reference.")
    anchor("short_peak","seven_stroke_peak",settings["anchors"]["pp_repeatable_pct_of_short_peak"],"Repeatable PP and start-power work.")
    anchor("one_minute","sixty_second",settings["anchors"]["an_60s_pct_of_one_minute"],"Approximately 60-second AN work.",watts["seven_stroke_peak"])
    anchor("rate_capped_sustained","thirty_min_rate_capped",settings["anchors"]["provisional_rate_capped_reference_pct_of_sustained"],"Rate-capped endurance benchmark; it does not define UT or threshold bands.")
    confidence="medium" if watts["two_k"] or anchors else "low"
    return {"algorithm_version":settings["algorithm_version"],"testing_block_id":current["block"].get("id"),"testing_block_label":current["block"].get("label"),"status":current["status"],"mode":"anchors_only","confidence":confidence,"metrics":current["metrics"],"anchors":anchors,"warnings":warnings,"assumptions":["An actual 2k is the primary integrated erg performance anchor when available.","Ratios are descriptive relationships among your own test results.","No predicted 2k, threshold, or population weakness label is generated."],"raw_tests":current["tests"],"testing_blocks":testing_blocks(profile),"longitudinal":trends,"plan_impacts":_plan_impact(trends),"two_k_watts":watts["two_k"]}

def target_for_band(power: dict, band: str) -> dict | None:
    key="short_peak" if band=="PP" else "one_minute" if band=="AN" else "two_k" if band=="TR" else None
    return next((a for a in power.get("anchors",[]) if a["name"]==key),None)
