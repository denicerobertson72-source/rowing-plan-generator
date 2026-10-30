"""Pure, evidence-bounded performance context for V3 workout selection.

This module describes the athlete's own dated tests.  It intentionally does
not predict performance, create population labels, or alter scheduled dose.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from .power_profile import longitudinal, testing_blocks
from .conversions import two_k_seconds_to_watts


PROFILE_VERSION = "v3-performance-profile-0.1"
_PROTOCOLS = ("two_k", "thirty_min_rate_capped_optional", "sixty_second", "seven_stroke_peak", "twenty_second_optional")


@dataclass(frozen=True)
class PerformanceTestEvidence:
    test_id: str
    protocol: str
    test_date: date | None
    watts: float | None
    time_seconds: float | None
    valid_for_profile: bool
    retained_for_profile: bool
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class AthletePerformanceProfile:
    source_test_ids: tuple[str, ...]
    tests: tuple[PerformanceTestEvidence, ...]
    two_k_seconds: float | None
    two_k_watts: float | None
    thirty_r20_watts: float | None
    sixty_second_watts: float | None
    seven_stroke_watts: float | None
    twenty_second_watts: float | None
    relationships: Mapping[str, float]
    longitudinal_deltas: Mapping[str, float]
    confidence: str
    evidence_completeness: str
    warnings: tuple[str, ...]
    provenance: tuple[str, ...]


@dataclass(frozen=True)
class PerformanceLimiterProfile:
    mode: str
    primary_focus: str
    secondary_focus: str | None
    evidence: tuple[str, ...]
    confidence: str
    tests_used: tuple[str, ...]
    tests_missing: tuple[str, ...]
    policy_version: str
    provenance: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class TrainingFocus:
    primary: str
    secondary: str | None
    source: str
    phase: str
    race_type: str
    days_to_race: int | None
    recovery_active: bool
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class TrainingDevelopmentBlock:
    block_id: str
    start_date: date
    end_date: date
    primary_focus: str
    secondary_focus: str | None
    season_phase: str
    race_context: str
    progression_family: str
    current_progression_step: int
    completed_key_exposures: int
    benchmark_intent: str | None
    retest_state: str
    completion_state: str
    provenance: tuple[str, ...]


def _parsed(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _watts(test: Mapping[str, Any]) -> float | None:
    for key in ("peak_watts", "average_watts", "value_watts"):
        if test.get(key) is not None:
            return float(test[key])
    if test.get("time_seconds") is not None and test.get("protocol") == "two_k":
        return two_k_seconds_to_watts(float(test["time_seconds"]))
    return None


def build_athlete_performance_profile(profile: Mapping[str, Any], *, as_of: date) -> AthletePerformanceProfile:
    """Use only explicitly valid test records already accepted by power_profile."""
    entries: list[PerformanceTestEvidence] = []
    current: dict[str, PerformanceTestEvidence] = {}
    warnings: list[str] = []
    for block in testing_blocks(dict(profile)):
        for raw in block.get("performance_tests", ()):
            if raw.get("protocol") not in _PROTOCOLS:
                continue
            valid = raw.get("valid_for_profile") is not False and raw.get("validity") in (None, "valid")
            evidence = PerformanceTestEvidence(
                str(raw.get("id") or f"{raw.get('protocol')}:{raw.get('test_date') or 'undated'}"),
                str(raw["protocol"]), _parsed(raw.get("test_date")), _watts(raw),
                float(raw["time_seconds"]) if raw.get("time_seconds") is not None else None,
                valid, raw.get("valid_for_profile") is True,
                ("profile.tests.testing_blocks",),
            )
            entries.append(evidence)
            if valid and (not current.get(evidence.protocol) or (evidence.test_date or date.min) >= (current[evidence.protocol].test_date or date.min)):
                current[evidence.protocol] = evidence
    values = {key: current.get(key).watts if current.get(key) else None for key in _PROTOCOLS}
    two = current.get("two_k")
    if not two:
        warnings.append("No valid dated 2K test is available; no integrated performance anchor is inferred.")
    relationships = {
        key: value for key, value in {
            "thirty_r20_to_two_k": values["thirty_min_rate_capped_optional"] / values["two_k"] if values["thirty_min_rate_capped_optional"] and values["two_k"] else None,
            "sixty_second_to_two_k": values["sixty_second"] / values["two_k"] if values["sixty_second"] and values["two_k"] else None,
            "seven_stroke_to_sixty_second": values["seven_stroke_peak"] / values["sixty_second"] if values["seven_stroke_peak"] and values["sixty_second"] else None,
            "seven_stroke_to_two_k": values["seven_stroke_peak"] / values["two_k"] if values["seven_stroke_peak"] and values["two_k"] else None,
        }.items() if value is not None
    }
    trends = longitudinal(testing_blocks(dict(profile)), as_of, 365)
    deltas = {key: value for key, value in (trends.get("rows", [])[-1] if trends.get("rows") else {}).items() if key.endswith("_pct_change") and value is not None}
    present = len(current)
    return AthletePerformanceProfile(
        tuple(sorted(item.test_id for item in current.values())), tuple(sorted(entries, key=lambda item: (item.protocol, item.test_date or date.min, item.test_id))),
        two.time_seconds if two else None, values["two_k"], values["thirty_min_rate_capped_optional"], values["sixty_second"], values["seven_stroke_peak"], values["twenty_second_optional"],
        dict(sorted(relationships.items())), dict(sorted(deltas.items())),
        "high" if present >= 4 else "medium" if present >= 2 else "low" if present else "unavailable",
        "complete" if all(current.get(key) for key in ("two_k", "thirty_min_rate_capped_optional", "sixty_second", "seven_stroke_peak")) else "partial" if present else "none",
        tuple(warnings), ("power_profile.testing_blocks", PROFILE_VERSION),
    )


def derive_performance_limiter(performance: AthletePerformanceProfile, *, coach_focus: str | None = None) -> PerformanceLimiterProfile:
    missing = tuple(key for key, value in (("two_k", performance.two_k_watts), ("thirty_r20", performance.thirty_r20_watts), ("sixty_second", performance.sixty_second_watts), ("seven_stroke", performance.seven_stroke_watts)) if value is None)
    if coach_focus:
        return PerformanceLimiterProfile("coach_defined", coach_focus, None, ("explicit coach/profile focus",), performance.confidence, performance.source_test_ids, missing, PROFILE_VERSION, ("coach_input",), "Current development priority supplied by coach or athlete.")
    # The app deliberately requires a complete explicit battery before using
    # its versioned relative heuristic; no population cutoffs are used.
    if missing:
        return PerformanceLimiterProfile("unknown", "insufficient_evidence", None, (), "unavailable" if len(missing) == 4 else "low", performance.source_test_ids, missing, PROFILE_VERSION, ("missing_test_evidence",), "Insufficient valid test relationships for a relative limiter interpretation.")
    ratios = performance.relationships
    focus = min(
        (("aerobic_strength", ratios.get("thirty_r20_to_two_k", 0)),
         ("anaerobic_lactic", ratios.get("sixty_second_to_two_k", 0)),
         ("anaerobic_alactic_peak_power", ratios.get("seven_stroke_to_sixty_second", 0))),
        key=lambda item: (item[1], item[0]),
    )[0]
    return PerformanceLimiterProfile("athlete_relative_heuristic", focus, "integrated_race_performance", tuple(sorted(f"{key}={value:.3f}" for key, value in ratios.items())), "medium", performance.source_test_ids, (), PROFILE_VERSION, ("athlete_relative_relationships", "evidence_informed_heuristic"), "Relative development priority from the athlete's own valid test relationships; not a universal weakness label.")


def derive_training_focus(limiter: PerformanceLimiterProfile, *, phase: str, race_type: str = "general", days_to_race: int | None = None, recovery_active: bool = False) -> TrainingFocus:
    if recovery_active:
        return TrainingFocus("technical_consolidation", None, "recovery", phase, race_type, days_to_race, True, ("active recovery constraint overrides development emphasis",))
    if days_to_race is not None and days_to_race <= 7:
        return TrainingFocus("taper_race_readiness", limiter.primary_focus if limiter.mode != "unknown" else None, "race_context", phase, race_type, days_to_race, False, ("upcoming race context takes precedence over development emphasis",))
    mapping = {"aerobic_strength": "aerobic_strength", "anaerobic_lactic": "anaerobic_lactic_development", "anaerobic_alactic_peak_power": "peak_power_development"}
    primary = mapping.get(limiter.primary_focus, "aerobic_development")
    return TrainingFocus(primary, limiter.secondary_focus, "performance_limiter" if limiter.mode != "unknown" else "season_phase", phase, race_type, days_to_race, False, ("limiter informs compatible workout form only",))


def derive_development_block(focus: TrainingFocus, *, planning_date: date, prior_diagnostics: Mapping[str, Any] | None = None) -> TrainingDevelopmentBlock:
    prior = (prior_diagnostics or {}).get("performance_development_block") if isinstance(prior_diagnostics, Mapping) else None
    if isinstance(prior, Mapping) and prior.get("primary_focus") == focus.primary and prior.get("end_date", "") >= planning_date.isoformat():
        return TrainingDevelopmentBlock(str(prior["block_id"]), _parsed(prior["start_date"]) or planning_date, _parsed(prior["end_date"]) or planning_date + timedelta(days=20), focus.primary, focus.secondary, focus.phase, focus.race_type, str(prior.get("progression_family", focus.primary)), int(prior.get("current_progression_step", 0)), int(prior.get("completed_key_exposures", 0)), prior.get("benchmark_intent"), str(prior.get("retest_state", "not_due")), "active", ("prior_plan_preserved",))
    return TrainingDevelopmentBlock(f"block:{planning_date.isoformat()}:{focus.primary}", planning_date, planning_date + timedelta(days=20), focus.primary, focus.secondary, focus.phase, focus.race_type, focus.primary, 0, 0, None, "insufficient_evidence" if focus.source == "season_phase" else "consider", "active", ("v3_generated",))
