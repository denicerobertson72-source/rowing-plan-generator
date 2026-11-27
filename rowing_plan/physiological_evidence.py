"""Typed, non-prescriptive physiological measurement evidence for V3.

This module deliberately normalizes the existing athlete-owned assessment
records.  It neither creates a training target nor selects a developmental
emphasis.  That remains a later R9F.3c decision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from .power_profile import testing_blocks


EVIDENCE_ARCHITECTURE_VERSION = "r9f3b-evidence-2"

_PROTOCOLS = {
    "two_k": "two_k_time_trial",
    "two_k_time_trial": "two_k_time_trial",
    "seven_stroke_peak": "seven_stroke_peak_power",
    "seven_stroke_peak_power": "seven_stroke_peak_power",
    "sixty_second": "sixty_second_maximal_effort",
    "sixty_second_maximal_effort": "sixty_second_maximal_effort",
    "twenty_second_optional": "twenty_second_maximal_effort",
    "twenty_second_maximal_effort": "twenty_second_maximal_effort",
    "thirty_min_rate_capped_maximal": "thirty_min_rate_capped_maximal",
    "thirty_min_rate_capped_submaximal": "thirty_min_rate_capped_submaximal",
    "one_k": "one_k_time_trial",
    "one_k_time_trial": "one_k_time_trial",
    "five_k": "five_k_time_trial",
    "five_k_time_trial": "five_k_time_trial",
    "six_k": "six_k_time_trial",
    "six_k_time_trial": "six_k_time_trial",
    "on_water": "on_water_assessment",
    "on_water_assessment": "on_water_assessment",
    "race": "race_performance",
    "race_performance": "race_performance",
    "training_monitoring": "standardized_training_monitoring",
    "standardized_training_monitoring": "standardized_training_monitoring",
    "strength": "strength_assessment",
    "strength_assessment": "strength_assessment",
}
_ERG_PROTOCOLS = {
    "two_k", "two_k_time_trial", "seven_stroke_peak", "seven_stroke_peak_power",
    "sixty_second", "sixty_second_maximal_effort", "twenty_second_optional", "twenty_second_maximal_effort",
    "thirty_min_rate_capped_optional", "thirty_min_rate_capped_maximal",
    "thirty_min_rate_capped_submaximal", "one_k", "one_k_time_trial", "five_k", "five_k_time_trial",
    "six_k", "six_k_time_trial",
}
_DOMAINS = (
    "integrated_rowing_performance", "neuromuscular_peak_power",
    "short_duration_sprint_performance", "anaerobic_performance",
    "aerobic_capacity", "sustainable_power_threshold",
    "aerobic_strength_endurance", "aerobic_efficiency", "fatigue_resistance",
    "maximum_strength", "technical_efficiency", "recovery_training_tolerance",
)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _nonnegative_int(value: Any) -> int | None:
    """Best-effort legacy normalization; invalid raw values remain absent."""
    try:
        normalized = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return normalized if normalized is None or normalized >= 0 else None


@dataclass(frozen=True)
class AssessmentOutcome:
    """One observed or derived value; zero remains distinct from absence."""

    key: str
    value: float | int | str | bool
    unit: str | None
    evidence_kind: str = "measured"  # measured | derived | proxy | inferred

    def __post_init__(self) -> None:
        if self.evidence_kind not in {"measured", "derived", "proxy", "inferred"}:
            raise ValueError("assessment_outcome_evidence_kind_is_invalid")


@dataclass(frozen=True)
class AssessmentMeasurement:
    """Immutable normalized view of an existing assessment fact.

    ``raw_protocol`` preserves the persisted spelling.  ``protocol`` is the
    extensible representation used by evidence diagnostics.  No conversion of
    an on-water outcome to erg power is ever performed here.
    """

    measurement_id: str
    protocol: str
    raw_protocol: str
    protocol_version: str | None
    modality: str
    assessment_date: date | None
    source: str | None
    validity: str
    effort_intent: str
    outcomes: tuple[AssessmentOutcome, ...]
    duration_seconds: int | None = None
    distance_meters: int | None = None
    equipment: tuple[tuple[str, str], ...] = ()
    conditions: tuple[tuple[str, str], ...] = ()
    data_quality: str = "not_reported"
    protocol_metadata: tuple[tuple[str, str], ...] = ()
    provenance: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.measurement_id or not self.protocol:
            raise ValueError("assessment_measurement_identity_is_required")
        if self.validity not in {"valid", "questionable", "invalid", "unknown"}:
            raise ValueError("assessment_measurement_validity_is_invalid")
        if self.effort_intent not in {"maximal", "submaximal", "unknown", "race"}:
            raise ValueError("assessment_measurement_effort_intent_is_invalid")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("assessment_measurement_duration_is_invalid")
        if self.distance_meters is not None and self.distance_meters < 0:
            raise ValueError("assessment_measurement_distance_is_invalid")
        object.__setattr__(self, "outcomes", tuple(sorted(self.outcomes, key=lambda item: item.key)))
        for field in ("equipment", "conditions", "protocol_metadata"):
            object.__setattr__(self, field, tuple(sorted(dict(getattr(self, field)).items())))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations))))


@dataclass(frozen=True)
class AssessmentEpisode:
    episode_id: str
    purpose: str
    start_date: date | None
    end_date: date | None
    measurement_ids: tuple[str, ...]
    season_context: str | None = None
    developmental_context: str | None = None
    comparability_notes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValueError("assessment_episode_dates_are_reversed")
        object.__setattr__(self, "measurement_ids", tuple(sorted(set(self.measurement_ids))))
        object.__setattr__(self, "comparability_notes", tuple(sorted(set(self.comparability_notes))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class EvidenceDomainAssessment:
    """Audit view, not a physiological diagnosis or recommendation."""

    domain: str
    measurement_ids: tuple[str, ...]
    modalities: tuple[str, ...]
    evidence_status: str  # available | missing
    evidence_modes: tuple[str, ...]  # direct | proxy | inferred
    latest_measurement_date: date | None
    oldest_measurement_date: date | None
    ages_days: tuple[int, ...]
    # These deliberately remain categorical.  A reported protocol/measurement
    # does not by itself justify confidence in a physiological interpretation
    # or a training recommendation.
    measurement_confidence: str
    interpretive_confidence: str
    reliability: str  # Backwards-readable summary of measurement confidence.
    completeness: str
    comparability: str
    longitudinal_consistency: str
    intended_uses: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    uncertainty: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.domain not in _DOMAINS:
            raise ValueError("evidence_domain_is_invalid")
        if self.evidence_status not in {"available", "missing"}:
            raise ValueError("evidence_domain_status_is_invalid")
        object.__setattr__(self, "measurement_ids", tuple(sorted(set(self.measurement_ids))))
        object.__setattr__(self, "modalities", tuple(sorted(set(self.modalities))))
        object.__setattr__(self, "evidence_modes", tuple(sorted(set(self.evidence_modes))))
        object.__setattr__(self, "ages_days", tuple(sorted(set(self.ages_days))))
        object.__setattr__(self, "intended_uses", tuple(sorted(set(self.intended_uses))))
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations))))
        object.__setattr__(self, "uncertainty", tuple(sorted(set(self.uncertainty))))


@dataclass(frozen=True)
class PhysiologicalEvidenceProfile:
    measurements: tuple[AssessmentMeasurement, ...]
    episodes: tuple[AssessmentEpisode, ...]
    domains: tuple[EvidenceDomainAssessment, ...]
    capability_description: tuple[str, ...]
    coverage_description: str
    provenance: tuple[str, ...] = (EVIDENCE_ARCHITECTURE_VERSION,)

    def to_dict(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            return value
        return encode(asdict(self))


def _protocol(raw: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    source = str(raw.get("protocol") or raw.get("test_type") or "other")
    if source in {"thirty_min_rate_capped_maximal", "thirty_min_rate_capped_submaximal"}:
        return source, source, ()
    if source == "thirty_min_rate_capped_optional":
        intent = str(raw.get("effort_intent") or "unknown")
        if intent == "maximal":
            return "thirty_min_rate_capped_maximal", source, ()
        if intent == "submaximal":
            return "thirty_min_rate_capped_submaximal", source, ()
        return "thirty_min_rate_capped_legacy_unspecified", source, (
            "legacy_r20_effort_intent_unspecified", "not_interpreted_as_threshold_cp_or_vo2max",
        )
    return _PROTOCOLS.get(source, "other_custom_assessment"), source, ()


def _modality(raw: Mapping[str, Any], raw_protocol: str) -> str:
    value = str(raw.get("modality") or "")
    if value:
        return value
    if raw_protocol in _ERG_PROTOCOLS:
        return "ergometer"
    if raw_protocol in {"race", "race_performance"}:
        return "race_performance"
    if raw_protocol in {"on_water", "on_water_assessment"}:
        return "on_water"
    if raw_protocol in {"strength", "strength_assessment"}:
        return "strength_assessment"
    if raw_protocol in {"training_monitoring", "standardized_training_monitoring"}:
        return "training_monitoring"
    return "unspecified"


def _validity(raw: Mapping[str, Any]) -> str:
    if raw.get("valid_for_profile") is False or raw.get("validity") == "invalid":
        return "invalid"
    if raw.get("protocol") == "thirty_min_rate_capped_optional" and raw.get("average_watts") is not None and float(raw["average_watts"]) > 2000:
        return "invalid"
    value = str(raw.get("validity") or "valid")
    return value if value in {"valid", "questionable", "invalid", "unknown"} else "unknown"


def _outcomes(raw: Mapping[str, Any], raw_protocol: str) -> tuple[AssessmentOutcome, ...]:
    """Return typed outcomes without mutating the athlete-owned source row.

    Older profile versions stored every manually entered power value in
    ``average_watts``.  The explicit ``seven_stroke_peak`` protocol and its
    original Profile label establish that this particular legacy value is the
    peak-power result, not a seven-stroke average.  The normalized evidence
    therefore names it ``peak_watts`` while measurement provenance records
    the legacy storage field.  Other protocols retain their stored semantics.
    """
    legacy_seven_stroke_peak = (
        raw_protocol in {"seven_stroke_peak", "seven_stroke_peak_power"}
        and raw.get("peak_watts") is None
        and raw.get("average_watts") is not None
    )
    fields = (
        ("time_seconds", "s"), ("result_seconds", "s"),
        ("average_watts", "W"), ("peak_watts", "W"),
        ("value_watts", "W"), ("distance_meters", "m"),
        ("average_split_seconds", "s/500m"), ("average_rate_spm", "spm"),
        ("peak_rate_spm", "spm"), ("average_hr", "bpm"),
        ("peak_hr", "bpm"), ("rpe", "0-10"),
    )
    values = [
        AssessmentOutcome(key, raw[key], unit)
        for key, unit in fields
        if raw.get(key) is not None and not (legacy_seven_stroke_peak and key == "average_watts")
    ]
    if legacy_seven_stroke_peak:
        values.append(AssessmentOutcome("peak_watts", raw["average_watts"], "W"))
    return tuple(values)


def _metadata(raw: Mapping[str, Any], names: Iterable[str]) -> tuple[tuple[str, str], ...]:
    return tuple((name, str(raw[name])) for name in names if raw.get(name) not in (None, ""))


def normalize_assessment_measurements(profile: Mapping[str, Any]) -> tuple[AssessmentMeasurement, ...]:
    """Normalize persisted test records without mutating or deduplicating them."""
    values: list[AssessmentMeasurement] = []
    for block_index, block in enumerate(testing_blocks(dict(profile))):
        for row_index, raw in enumerate(block.get("performance_tests", ())):
            if not isinstance(raw, Mapping):
                continue
            protocol, raw_protocol, protocol_limits = _protocol(raw)
            raw_id = raw.get("id") or f"{block.get('id') or block_index}:{raw_protocol}:{raw.get('test_date') or raw.get('date') or row_index}"
            duration = raw.get("duration_seconds")
            if duration is None and raw_protocol == "thirty_min_rate_capped_optional":
                duration = 1800
            date_value = _date(raw.get("test_date") or raw.get("date"))
            data_quality = str(raw.get("data_quality") or ("reported" if raw.get("source") else "not_reported"))
            limits = list(protocol_limits)
            modality = _modality(raw, raw_protocol)
            condition_keys = (
                "conditions", "course", "direction", "speed_reference", "wind", "current",
                "water_conditions", "weather", "crew_context", "interruptions",
            )
            if modality == "on_water" and not any(raw.get(key) not in (None, "") for key in condition_keys):
                limits.append("on_water_conditions_not_reported")
            legacy_seven_stroke_peak = (
                raw_protocol in {"seven_stroke_peak", "seven_stroke_peak_power"}
                and raw.get("peak_watts") is None
                and raw.get("average_watts") is not None
            )
            provenance = ["profile.tests.testing_blocks", f"testing_block:{block.get('id') or block_index}"]
            protocol_metadata = list(_metadata(raw, (
                "rate_cap_spm", "rate_compliance", "power_source", "power_calibration", "test_type",
            )))
            if legacy_seven_stroke_peak:
                provenance.append("seven_stroke_protocol_declares_peak_power_from_legacy_average_watts_storage")
                protocol_metadata.extend((
                    ("normalized_power_field", "peak_watts"),
                    ("source_power_field", "average_watts"),
                ))
            values.append(AssessmentMeasurement(
                str(raw_id), protocol, raw_protocol, _text(raw.get("protocol_version")), modality,
                date_value, _text(raw.get("source")), _validity(raw),
                str(raw.get("effort_intent") or ("race" if modality == "race_performance" else "unknown")),
                _outcomes(raw, raw_protocol), _nonnegative_int(duration), _nonnegative_int(raw.get("distance_meters")),
                _metadata(raw, ("erg_model", "drag_factor", "boat_class", "sculling_or_sweep", "rigging")),
                _metadata(raw, ("course", "direction", "speed_reference", "wind", "current", "water_conditions", "weather", "crew_context", "interruptions")),
                data_quality,
                tuple(protocol_metadata), tuple(provenance), tuple(limits),
            ))
    return tuple(sorted(values, key=lambda item: (item.assessment_date or date.min, item.measurement_id)))


def normalize_assessment_episodes(
    profile: Mapping[str, Any], measurements: Iterable[AssessmentMeasurement],
) -> tuple[AssessmentEpisode, ...]:
    by_id = {item.measurement_id for item in measurements}
    values: list[AssessmentEpisode] = []
    for index, block in enumerate(testing_blocks(dict(profile))):
        identifiers = tuple(
            str(row.get("id") or f"{block.get('id') or index}:{row.get('protocol') or row.get('test_type') or 'other'}:{row.get('test_date') or row.get('date') or row_index}")
            for row_index, row in enumerate(block.get("performance_tests", ())) if isinstance(row, Mapping)
        )
        identifiers = tuple(item for item in identifiers if item in by_id)
        if identifiers:
            values.append(AssessmentEpisode(
                str(block.get("id") or f"testing-block:{index}"), str(block.get("label") or "existing_testing_block"),
                _date(block.get("start_date")), _date(block.get("end_date")), identifiers,
                _text(block.get("phase_id")), _text(block.get("developmental_context")),
                ("existing_testing_block_grouping",), ("profile.tests.testing_blocks",),
            ))
    extra = profile.get("tests", {}).get("assessment_episodes", ()) if isinstance(profile.get("tests"), Mapping) else ()
    for index, raw in enumerate(extra):
        if not isinstance(raw, Mapping):
            continue
        identifiers = tuple(str(item) for item in raw.get("measurement_ids", ()) if str(item) in by_id)
        if not identifiers:
            continue
        values.append(AssessmentEpisode(
            str(raw.get("id") or f"assessment-episode:{index}"), str(raw.get("purpose") or "assessment_episode"),
            _date(raw.get("start_date")), _date(raw.get("end_date")), identifiers,
            _text(raw.get("season_context")), _text(raw.get("developmental_context")),
            tuple(str(item) for item in raw.get("comparability_notes", ()) if item), ("profile.tests.assessment_episodes",),
        ))
    return tuple(sorted(values, key=lambda item: (item.start_date or date.min, item.episode_id)))


def _domains_for(measurement: AssessmentMeasurement) -> tuple[tuple[str, str, str], ...]:
    protocol = measurement.protocol
    if protocol == "two_k_time_trial":
        return (("integrated_rowing_performance", "direct", "integrated_erg_performance_context"),)
    if protocol == "seven_stroke_peak_power":
        return (("neuromuscular_peak_power", "direct", "peak_power_monitoring_context"),)
    if protocol in {"sixty_second_maximal_effort", "twenty_second_maximal_effort", "one_k_time_trial"}:
        return (("short_duration_sprint_performance", "direct", "short_duration_performance_context"),
                ("anaerobic_performance", "proxy", "field_test_proxy_not_direct_physiological_measure"))
    if protocol == "thirty_min_rate_capped_maximal":
        return (("aerobic_strength_endurance", "direct", "rate_capped_sustained_performance_context"),
                ("sustainable_power_threshold", "proxy", "not_interpreted_as_measured_threshold_cp_or_vo2max"))
    if protocol == "thirty_min_rate_capped_submaximal":
        return (("aerobic_efficiency", "proxy", "submaximal_monitoring_requires_comparable_conditions"),)
    if protocol == "thirty_min_rate_capped_legacy_unspecified":
        return (("aerobic_strength_endurance", "proxy", "legacy_effort_intent_unknown"),)
    if protocol in {"five_k_time_trial", "six_k_time_trial"}:
        return (("aerobic_capacity", "proxy", "sustained_erg_performance_proxy"),)
    if protocol == "on_water_assessment":
        return (("technical_efficiency", "proxy", "on_water_environment_and_crew_comparability_required"),
                ("integrated_rowing_performance", "proxy", "on_water_not_converted_to_ergometer_equivalent"))
    if protocol == "race_performance":
        return (("integrated_rowing_performance", "direct", "race_result_is_context_dependent"),)
    if protocol == "standardized_training_monitoring":
        return (("aerobic_efficiency", "proxy", "comparable_standardized_session_required"),
                ("recovery_training_tolerance", "proxy", "training_response_not_ready_for_automatic_adjustment"))
    if protocol == "strength_assessment":
        return (("maximum_strength", "direct", "standardized_strength_protocol_required"),)
    return ()


def _domain_assessments(measurements: Iterable[AssessmentMeasurement], *, as_of: date) -> tuple[EvidenceDomainAssessment, ...]:
    grouped: dict[str, list[tuple[AssessmentMeasurement, str, str]]] = {domain: [] for domain in _DOMAINS}
    for measurement in measurements:
        if measurement.validity == "invalid":
            continue
        for domain, mode, limitation in _domains_for(measurement):
            grouped[domain].append((measurement, mode, limitation))
    values = []
    for domain in _DOMAINS:
        rows = grouped[domain]
        if not rows:
            values.append(EvidenceDomainAssessment(
                domain, (), (), "missing", (), None, None, (), "not_assessed", "not_assessed",
                "not_assessed", "no_evidence", "not_assessed", "not_assessed", (), ("missing_evidence_is_not_interpreted_as_weakness",),
                ("no_measurement_available",),
            ))
            continue
        measured = [item for item, _mode, _limitation in rows]
        dated = [item.assessment_date for item in measured if item.assessment_date]
        modes = [mode for _item, mode, _limitation in rows]
        limits = [limitation for _item, _mode, limitation in rows]
        limits.extend(limitation for item in measured for limitation in item.limitations)
        repeated = len({(item.protocol, item.modality) for item in measured}) < len(measured)
        modalities = {item.modality for item in measured}
        on_water = [item for item in measured if item.modality == "on_water"]
        condition_signatures = {
            (item.equipment, item.conditions) for item in on_water
        }
        # For on-water evidence, repeatability alone is not comparability.
        # Conditions/equipment are retained rather than normalized into a
        # fabricated erg equivalent.
        comparable_on_water = bool(on_water) and all(item.conditions for item in on_water) and len(condition_signatures) == 1
        comparability = (
            "potentially_comparable" if repeated and len(modalities) == 1 and (not on_water or comparable_on_water)
            else "limited" if len(modalities) > 1 or on_water
            else "not_yet_assessed"
        )
        longitudinal = "repeated_measurements_available" if repeated else "single_measurement_or_nonrepeated"
        measurement_confidence = "protocol_and_quality_reported" if all(
            item.data_quality not in {"not_reported", "unknown"} for item in measured
        ) else "metadata_limited"
        # This is a deliberately non-numeric audit label.  R9F.3b does not
        # turn any assessment into a physiological diagnosis or prescription.
        interpretive_confidence = (
            "context_limited_not_prescriptive"
            if "proxy" in modes or comparability != "potentially_comparable"
            else "protocol_context_only_not_prescriptive"
        )
        values.append(EvidenceDomainAssessment(
            domain, tuple(item.measurement_id for item in measured), tuple(item.modality for item in measured), "available", tuple(modes),
            max(dated) if dated else None, min(dated) if dated else None,
            tuple((as_of - item).days for item in dated if item <= as_of), measurement_confidence,
            interpretive_confidence, measurement_confidence,
            "complementary_evidence" if len({item.protocol for item in measured}) > 1 else "single_protocol_evidence",
            comparability, longitudinal,
            tuple(sorted(set(limitation for _item, _mode, limitation in rows))), tuple(sorted(set(limits))),
            ("measurement_age_is_reported_without_an_automatic_expiration_policy",),
        ))
    return tuple(values)


def _capability(measurements: tuple[AssessmentMeasurement, ...], domains: tuple[EvidenceDomainAssessment, ...]) -> tuple[str, ...]:
    values = ["foundation_context_uses_existing_goals_experience_availability_and_training_context"]
    available = [item for item in domains if item.evidence_status == "available"]
    if measurements:
        values.append("basic_performance_evidence_available")
    if len({item.domain for item in available}) > 1:
        # Complementary assessed domains do not establish a comprehensive
        # physiological profile.  The domain coverage is reported separately.
        values.append("complementary_performance_evidence_available")
        # Preserve the R9F.3b diagnostic token for existing consumers; the
        # new coverage_description prevents it from implying completeness.
        values.append("complementary_physiological_evidence_available")
    if any(
        item.longitudinal_consistency == "repeated_measurements_available"
        and item.comparability == "potentially_comparable"
        for item in domains
    ) and any(item.domain == "recovery_training_tolerance" and item.evidence_status == "available" for item in domains):
        values.append("adaptive_profile_longitudinal_response_evidence_available")
    if not measurements:
        values.append("incomplete_evidence_does_not_block_useful_training")
    return tuple(values)


def _coverage_description(domains: tuple[EvidenceDomainAssessment, ...]) -> str:
    """Describe represented domains without turning a count into a score."""
    if not any(item.evidence_status == "available" for item in domains):
        return "no_formal_domain_evidence"
    if any(item.evidence_status == "missing" for item in domains):
        return "partial_domain_coverage_not_comprehensive"
    return "broad_domain_coverage_without_prescriptive_interpretation"


def build_physiological_evidence_profile(profile: Mapping[str, Any], *, as_of: date) -> PhysiologicalEvidenceProfile:
    measurements = normalize_assessment_measurements(profile)
    episodes = normalize_assessment_episodes(profile, measurements)
    domains = _domain_assessments(measurements, as_of=as_of)
    return PhysiologicalEvidenceProfile(
        measurements, episodes, domains, _capability(measurements, domains),
        _coverage_description(domains),
    )
