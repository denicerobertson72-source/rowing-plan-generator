"""Transparent intensity-provider hierarchy; power profile never replaces it."""
from __future__ import annotations
from .models import Band
from .conversions import two_k_seconds_to_watts

NAMES = ["UT3", "UT2", "UT1", "AT", "TR", "AN", "PP"]
DOMAINS = {"UT3":"low", "UT2":"low", "UT1":"moderate", "AT":"moderate", "TR":"high", "AN":"anaerobic", "PP":"peak"}

def build_intensity_profile(profile: dict, config: dict) -> list[dict]:
    tests = profile.get("tests", {})
    coach = tests.get("coach_defined_bands")
    spm = config["intensity"]["default_spm"]
    if tests.get("lt1_hr") is not None or tests.get("lt2_hr") is not None:
        lt1, lt2 = tests.get("lt1_hr"), tests.get("lt2_hr")
        fractions = {"UT3":(.55,.65),"UT2":(.65,.75),"UT1":(.75,.85),"AT":(.85,.92),"TR":(.92,1),"AN":(None,None),"PP":(None,None)}
        bands=[]
        for n in NAMES:
            lo, hi = fractions[n]
            bands.append(Band(n, DOMAINS[n], int(lt1*lo) if lt1 and lo else None, int((lt2 or lt1)*hi) if (lt2 or lt1) and hi else None, spm_low=spm[n][0] if spm[n] else None, spm_high=spm[n][1] if spm[n] else None, method="measured_threshold", confidence="high", assumptions=["LT1/LT2 supplied by athlete or coach."]).to_dict())
        return bands
    if coach:
        return [dict(Band(n, DOMAINS[n], method="coach_defined", confidence="high", assumptions=["Coach-defined band."]).to_dict(), **coach.get(n, {})) for n in NAMES]
    use_2k = bool(profile.get("preferences", {}).get("enable_2k_power_profile")) and tests.get("erg_2k_seconds")
    hrr = config["intensity"]["hrr_fallback"]
    rest, maximum = tests.get("resting_hr"), tests.get("max_hr")
    base_watts = two_k_seconds_to_watts(tests["erg_2k_seconds"]) if use_2k else None
    two = config["intensity"]["two_k_power_profile"]["bands"]
    out=[]
    for n in NAMES:
        f = hrr.get(n)
        hr_lo = int(rest+(maximum-rest)*f[0]) if f and rest and maximum else None
        hr_hi = int(rest+(maximum-rest)*f[1]) if f and rest and maximum else None
        wp = two.get(n) if use_2k else None
        out.append(Band(n, DOMAINS[n], hr_lo, hr_hi, base_watts*wp[0] if wp else None, base_watts*wp[1] if wp else None, spm[n][0] if spm[n] else None, spm[n][1] if spm[n] else None, method="2k_config" if use_2k else "hrr_fallback", confidence="medium" if use_2k and rest and maximum else "low", assumptions=["2k configuration is provisional."] if use_2k else ["HRR/RPE fallback; not measured thresholds."]).to_dict())
    return out
