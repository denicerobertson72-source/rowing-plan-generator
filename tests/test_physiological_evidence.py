from __future__ import annotations

import json
from datetime import date

from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report
from rowing_plan.physiological_evidence import AssessmentOutcome, build_physiological_evidence_profile
from services.api.tests.disposable_browser_fixture import synthetic_profile


AS_OF = date(2026, 11, 25)


def _profile(tests=(), *, episodes=()):
    return {"tests": {"testing_blocks": [{
        "id": "winter-baseline", "label": "Winter baseline", "start_date": "2026-01-04",
        "end_date": "2026-01-14", "performance_tests": list(tests),
    }], "assessment_episodes": list(episodes)}}


def _test(identifier, protocol, day="2026-01-04", **values):
    return {"id": identifier, "protocol": protocol, "test_date": day,
            "valid_for_profile": True, "source": "manual", **values}


def _domains(evidence):
    return {item.domain: item for item in evidence.domains}


def test_no_assessment_keeps_all_domains_missing_without_blocking_foundation_context():
    evidence = build_physiological_evidence_profile(_profile(), as_of=AS_OF)
    assert evidence.measurements == ()
    assert all(item.evidence_status == "missing" for item in evidence.domains)
    assert "incomplete_evidence_does_not_block_useful_training" in evidence.capability_description


def test_single_historical_erg_measurement_is_preserved_with_actual_age_not_expiration():
    evidence = build_physiological_evidence_profile(
        _profile((_test("2k-old", "two_k", "2025-11-21", time_seconds=496),)), as_of=AS_OF,
    )
    measurement = evidence.measurements[0]
    integrated = _domains(evidence)["integrated_rowing_performance"]
    assert measurement.modality == "ergometer"
    assert measurement.assessment_date == date(2025, 11, 21)
    assert any(item.key == "time_seconds" and item.value == 496 for item in measurement.outcomes)
    assert integrated.evidence_status == "available"
    assert integrated.ages_days == ((AS_OF - date(2025, 11, 21)).days,)
    assert "measurement_age_is_reported_without_an_automatic_expiration_policy" in integrated.uncertainty


def test_canonical_protocol_identifiers_are_additive_and_infer_their_existing_modality():
    evidence = build_physiological_evidence_profile(
        _profile((_test("2k-canonical", "two_k_time_trial", time_seconds=496),)), as_of=AS_OF,
    )
    assert evidence.measurements[0].protocol == "two_k_time_trial"
    assert evidence.measurements[0].modality == "ergometer"


def test_complementary_winter_battery_preserves_distinct_direct_and_proxy_domains():
    evidence = build_physiological_evidence_profile(_profile((
        _test("2k", "two_k", time_seconds=496),
        _test("7", "seven_stroke_peak", peak_watts=287),
        _test("60", "sixty_second", average_watts=220),
        _test("r20", "thirty_min_rate_capped_maximal", average_watts=145, duration_seconds=1800,
              rate_cap_spm=20, effort_intent="maximal", drag_factor=110),
    )), as_of=AS_OF)
    domains = _domains(evidence)
    assert domains["neuromuscular_peak_power"].evidence_modes == ("direct",)
    assert domains["anaerobic_performance"].evidence_modes == ("proxy",)
    assert domains["aerobic_strength_endurance"].evidence_modes == ("direct",)
    threshold = domains["sustainable_power_threshold"]
    assert threshold.evidence_modes == ("proxy",)
    assert "not_interpreted_as_measured_threshold_cp_or_vo2max" in threshold.intended_uses
    assert "complementary_physiological_evidence_available" in evidence.capability_description


def test_legacy_seven_stroke_peak_protocol_normalizes_generic_average_storage_without_mutating_source():
    profile = _profile((_test("seven", "seven_stroke_peak", average_watts=287),))
    source = profile["tests"]["testing_blocks"][0]["performance_tests"][0]
    evidence = build_physiological_evidence_profile(profile, as_of=AS_OF)
    measurement = evidence.measurements[0]
    assert source == {"id": "seven", "protocol": "seven_stroke_peak", "test_date": "2026-01-04",
                      "valid_for_profile": True, "source": "manual", "average_watts": 287}
    assert [(item.key, item.value) for item in measurement.outcomes] == [("peak_watts", 287)]
    assert "seven_stroke_protocol_declares_peak_power_from_legacy_average_watts_storage" in measurement.provenance
    assert dict(measurement.protocol_metadata) == {
        "normalized_power_field": "peak_watts", "source_power_field": "average_watts",
    }
    assert _domains(evidence)["neuromuscular_peak_power"].evidence_status == "available"


def test_explicit_seven_stroke_peak_and_ambiguous_historical_average_remain_distinct():
    explicit = build_physiological_evidence_profile(
        _profile((_test("seven-explicit", "seven_stroke_peak", peak_watts=300),)), as_of=AS_OF,
    ).measurements[0]
    ambiguous_evidence = build_physiological_evidence_profile(
        _profile((_test("ambiguous", "other_historical_protocol", average_watts=300),)), as_of=AS_OF,
    )
    ambiguous = ambiguous_evidence.measurements[0]
    assert [(item.key, item.value) for item in explicit.outcomes] == [("peak_watts", 300)]
    assert "seven_stroke_protocol_declares_peak_power_from_legacy_average_watts_storage" not in explicit.provenance
    assert [(item.key, item.value) for item in ambiguous.outcomes] == [("average_watts", 300)]
    assert _domains(ambiguous_evidence)["neuromuscular_peak_power"].evidence_status == "missing"


def test_maximal_and_submaximal_r20_are_distinct_and_legacy_r20_does_not_invent_intent():
    maximal = build_physiological_evidence_profile(_profile((
        _test("r20-max", "thirty_min_rate_capped_maximal", average_watts=150, duration_seconds=1800,
              effort_intent="maximal"),
    )), as_of=AS_OF).measurements[0]
    submaximal = build_physiological_evidence_profile(_profile((
        _test("r20-sub", "thirty_min_rate_capped_submaximal", average_watts=120, duration_seconds=1800,
              effort_intent="submaximal"),
    )), as_of=AS_OF).measurements[0]
    legacy = build_physiological_evidence_profile(_profile((
        _test("r20-legacy", "thirty_min_rate_capped_optional", average_watts=140),
    )), as_of=AS_OF).measurements[0]
    assert (maximal.protocol, maximal.effort_intent) == ("thirty_min_rate_capped_maximal", "maximal")
    assert (submaximal.protocol, submaximal.effort_intent) == ("thirty_min_rate_capped_submaximal", "submaximal")
    assert legacy.protocol == "thirty_min_rate_capped_legacy_unspecified"
    assert "legacy_r20_effort_intent_unspecified" in legacy.limitations


def test_on_water_evidence_remains_modality_specific_and_never_becomes_erg_power():
    evidence = build_physiological_evidence_profile(_profile((
        _test("water", "on_water", "2026-11-20", modality="on_water", distance_meters=6000,
              average_split_seconds=125, average_rate_spm=20, boat_class="1x", course="river",
              direction="upstream", speed_reference="gps", wind="crosswind", current="moderate"),
        _test("erg", "two_k", "2025-11-21", time_seconds=496),
    )), as_of=AS_OF)
    water = next(item for item in evidence.measurements if item.measurement_id == "water")
    technical = _domains(evidence)["technical_efficiency"]
    assert water.modality == "on_water"
    assert not any(item.key == "average_watts" for item in water.outcomes)
    assert "on_water_not_converted_to_ergometer_equivalent" in _domains(evidence)["integrated_rowing_performance"].intended_uses
    assert technical.comparability == "limited"


def test_on_water_only_athlete_and_missing_optional_conditions_remain_valid_without_erg_assumptions():
    evidence = build_physiological_evidence_profile(_profile((
        _test("water-only", "on_water", "2026-11-20", modality="on_water", distance_meters=6000,
              average_split_seconds=125),
    )), as_of=AS_OF)
    water = evidence.measurements[0]
    domains = _domains(evidence)
    assert water.modality == "on_water"
    assert "on_water_conditions_not_reported" in water.limitations
    assert domains["technical_efficiency"].evidence_status == "available"
    assert domains["integrated_rowing_performance"].evidence_modes == ("proxy",)
    assert all(item.modality != "ergometer" for item in evidence.measurements)


def test_repeated_on_water_evidence_requires_comparable_conditions_and_typed_outcomes_support_inference_without_creating_one():
    noncomparable = build_physiological_evidence_profile(_profile((
        _test("water-a", "on_water", "2026-10-01", modality="on_water", distance_meters=6000,
              wind="headwind", boat_class="1x", speed_reference="gps"),
        _test("water-b", "on_water", "2026-11-01", modality="on_water", distance_meters=6000,
              wind="tailwind", boat_class="2x", speed_reference="gps"),
    )), as_of=AS_OF)
    assert _domains(noncomparable)["technical_efficiency"].comparability == "limited"
    # The typed measurement vocabulary can represent a future R9F.3c
    # inference, but R9F.3b intentionally emits no inferred characteristic.
    inferred = AssessmentOutcome("example", "context_only", None, "inferred")
    assert inferred.evidence_kind == "inferred"
    assert "inferred" not in _domains(noncomparable)["technical_efficiency"].evidence_modes


def test_invalid_measurement_and_measured_zero_are_preserved_but_invalid_is_not_evidence():
    evidence = build_physiological_evidence_profile(_profile((
        _test("invalid", "sixty_second", average_watts=220, validity="invalid", valid_for_profile=False),
        _test("zero", "training_monitoring", average_watts=0, modality="training_monitoring"),
    )), as_of=AS_OF)
    invalid = next(item for item in evidence.measurements if item.measurement_id == "invalid")
    zero = next(item for item in evidence.measurements if item.measurement_id == "zero")
    assert invalid.validity == "invalid"
    assert any(item.key == "average_watts" and item.value == 0 for item in zero.outcomes)
    assert _domains(evidence)["short_duration_sprint_performance"].evidence_status == "missing"


def test_episodes_are_additive_can_span_days_and_keep_historical_measurements():
    evidence = build_physiological_evidence_profile(_profile((
        _test("2k-jan", "two_k", "2026-01-04", time_seconds=500),
        _test("2k-feb", "two_k", "2026-02-08", time_seconds=490),
    ), episodes=({
        "id": "midwinter", "purpose": "midwinter_reassessment", "start_date": "2026-02-08",
        "end_date": "2026-02-10", "measurement_ids": ["2k-feb"],
    },)), as_of=AS_OF)
    assert [item.measurement_id for item in evidence.measurements] == ["2k-jan", "2k-feb"]
    assert {item.episode_id for item in evidence.episodes} == {"winter-baseline", "midwinter"}
    integrated = _domains(evidence)["integrated_rowing_performance"]
    assert integrated.longitudinal_consistency == "repeated_measurements_available"
    assert integrated.comparability == "potentially_comparable"
    assert integrated.measurement_confidence == "protocol_and_quality_reported"
    assert integrated.interpretive_confidence == "protocol_context_only_not_prescriptive"


def test_evidence_output_excludes_private_readiness_or_free_text_and_does_not_change_legacy_consumers():
    profile = _profile((_test("2k", "two_k", time_seconds=496),))
    profile["private_readiness"] = {"notes": "private health detail", "hrv": 12}
    profile["tests"]["notes"] = "private assessment narrative"
    evidence = build_physiological_evidence_profile(profile, as_of=AS_OF).to_dict()
    rendered = json.dumps(evidence)
    assert "private health detail" not in rendered
    assert "private assessment narrative" not in rendered
    assert evidence["measurements"][0]["raw_protocol"] == "two_k"


def test_diagnostic_exposes_evidence_without_changing_r2_r3_r4_r5_or_r6_contracts():
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-16", "end_date": "2026-11-22"}
    profile["tests"] = _profile((
        _test("2k", "two_k", "2025-11-21", time_seconds=496),
        _test("60", "sixty_second", "2026-01-04", average_watts=220),
        _test("7", "seven_stroke_peak", "2026-01-14", peak_watts=287),
    ))["tests"]
    report = generate_v3_diagnostic_report(profile, planning_date=date(2026, 11, 16))
    evidence = report["physiological_evidence"]
    assert evidence["legacy_limiter"]["status"] == "legacy_compatibility_heuristic_not_physiological_diagnosis"
    assert {item["measurement_id"] for item in evidence["measurements"]} == {"2k", "60", "7"}
    assert report["conservation"]["pass"]
    assert report["r6_results"]
    assert "PHYSIOLOGICAL MEASUREMENT EVIDENCE" in report["human_readable"]
    json.dumps(evidence)
