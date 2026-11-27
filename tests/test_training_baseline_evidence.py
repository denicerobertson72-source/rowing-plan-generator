from datetime import date

from rowing_plan.planning_domain import CurrentTrainingSource, CurrentTrainingState
from rowing_plan.training_baseline_evidence import (
    BASELINE_EVIDENCE_VERSION,
    build_training_baseline_and_tolerance_evidence,
)


AS_OF = date(2026, 11, 18)


def _profile(**athlete_updates):
    return {
        "athlete": {"current_weekly_training_minutes": 500, "current_approx_weekly_rowing_minutes": 240, **athlete_updates},
        "training_context": {"current_strength_sessions_per_week": 3},
    }


def _state():
    return CurrentTrainingState(
        CurrentTrainingSource.MIXED, observation_window_days=21,
        approximate_total_physical_minutes=500, approximate_rowing_minutes=240,
        strength_exposures=3, confidence="moderate",
    )


def _ledger(exposures=()):
    return {"exposures": list(exposures)}


def _actual(day, *, minutes=60, category="rowing_specific", modality="erg", rpe=None, band=None):
    return {
        "exposure_id": f"actual:{day}:{modality}", "date": day,
        "status": "completed_actual", "physical_duration_minutes": minutes,
        "physical_category": category, "modality": modality,
        "reported_rpe": rpe, "physiological_band": band,
    }


def _contracts(profile=None, exposures=()):
    return build_training_baseline_and_tolerance_evidence(
        profile or _profile(), exposure_ledger=_ledger(exposures),
        current_training_state=_state(), planning_date=AS_OF,
    )


def _reported(contract, metric, *, known=True):
    return [item for item in contract.to_dict()["reported_observations"] if item["metric"] == metric and item["known"] is known]


def _window(contract, days):
    return next(item for item in contract.to_dict()["completed_observation_windows"] if item["window_days"] == days)


def _observation(window, metric):
    return next(item for item in window["observations"] if item["metric"] == metric)


def test_explicit_reported_overall_rowing_and_strength_frequency_are_characterized_without_inferred_minutes():
    baseline, tolerance = _contracts()

    assert baseline.version == BASELINE_EVIDENCE_VERSION
    assert {item["value"] for item in _reported(baseline, "total_physical_minutes")} == {500}
    assert {item["value"] for item in _reported(baseline, "rowing_minutes")} == {240}
    assert {item["value"] for item in _reported(baseline, "strength_exposures")} == {3}
    assert _reported(baseline, "strength_minutes", known=False)[0]["value"] is None
    assert _reported(baseline, "general_aerobic_minutes", known=False)[0]["value"] is None
    assert tolerance.to_dict()["completion_alone_establishes_favorable_tolerance"] is False


def test_explicit_zero_is_known_and_distinct_from_an_absent_reported_value():
    profile = _profile(current_approx_weekly_rowing_minutes=0)
    baseline, _ = _contracts(profile)

    zero = _reported(baseline, "rowing_minutes", known=True)
    assert any(item["value"] == 0 and item["explicit_zero"] for item in zero)
    unknown = _reported(baseline, "strength_minutes", known=False)
    assert unknown and unknown[0]["explicit_zero"] is False


def test_sparse_completed_history_is_observed_not_inactivity_and_remains_separate_from_reported_baseline():
    baseline, tolerance = _contracts(exposures=(
        _actual("2026-11-10"), _actual("2026-11-14", minutes=45, category="strength", modality="strength"),
    ))

    window = _window(baseline, 14)
    assert _observation(window, "total_physical_minutes")["value"] == 105
    assert window["coverage"] == "observed_completed_records_only"
    assert window["missing_dates_are_not_rest"] is True
    assert "sparse_completed_history_does_not_establish_inactivity_or_rest" in baseline.to_dict()["limitations"]
    assert tolerance.to_dict()["pathways"][1]["availability"] == "partial"


def test_reported_and_completed_evidence_conflict_is_preserved_not_averaged():
    baseline, _ = _contracts(exposures=(
        _actual("2026-10-28"), _actual("2026-11-04"), _actual("2026-11-10"), _actual("2026-11-14"),
    ))

    conflicts = baseline.to_dict()["evidence_conflicts"]
    assert any(item["metric"] == "total_physical_minutes" and item["reported_value"] == 500 for item in conflicts)
    assert all(item["status"] == "different_evidence_preserved_not_merged" for item in conflicts)


def test_observation_windows_are_half_open_and_prospective_calendar_is_not_completed_history():
    baseline, _ = _contracts(exposures=(
        _actual("2026-11-11", minutes=20),  # included in 7d: [Nov 11, Nov 18)
        _actual("2026-11-18", minutes=90),  # planning day: intentionally excluded
        {**_actual("2026-11-17", minutes=70), "status": "generated_future"},
    ))

    seven = _window(baseline, 7)
    assert seven["window_start"] == "2026-11-11"
    assert seven["as_of_date"] == "2026-11-18"
    assert _observation(seven, "total_physical_minutes")["value"] == 20
    assert seven["observed_completed_dates"] == ["2026-11-11"]


def test_modality_and_category_evidence_only_exist_when_completed_records_support_them():
    baseline, _ = _contracts(exposures=(
        _actual("2026-11-15", minutes=30, category="general_aerobic", modality="bike"),
    ))

    window = _window(baseline, 7)
    assert _observation(window, "general_aerobic_minutes")["value"] == 30
    assert _observation(window, "bike_minutes")["value"] == 30
    assert not any(item["metric"] == "elliptical_minutes" for item in window["observations"])
    assert any(item["scope"] == "modality" for item in window["session_duration_evidence"])
    bike_duration = next(item for item in window["session_duration_evidence"] if item["scope"] == "modality" and item["value"] == "bike")
    assert (bike_duration["minimum_minutes"], bike_duration["maximum_minutes"]) == (30, 30)


def test_tolerance_marks_minimal_pathway_unavailable_without_authorized_confirmation_or_private_readiness():
    _baseline, tolerance = _contracts(exposures=(_actual("2026-11-15", rpe=4),))

    payload = tolerance.to_dict()
    pathways = {item["pathway"]: item for item in payload["pathways"]}
    assert pathways["minimal"]["availability"] == "unavailable"
    assert "athlete_confirmation_recovery" in pathways["minimal"]["missing_contracts"]
    assert payload["private_readiness_used"] is False
    assert "no_composite_fatigue_or_recovery_score" in payload["uncertainty"]


def test_tolerance_surfaces_a_non_sensitive_reported_restriction_without_exposing_its_free_text():
    profile = _profile()
    profile["training_context"] = {
        "current_strength_sessions_per_week": 3,
        "training_restriction_source": "coach",
        "training_restriction_summary": "private detail must not be exposed",
    }
    _baseline, tolerance = _contracts(profile)

    codes = tolerance.to_dict()["explicit_restriction_codes"]
    assert "reported_training_restriction_source:coach" in codes
    assert "reported_training_restriction_summary_present" in codes
    assert all("private detail" not in code for code in codes)
