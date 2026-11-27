from datetime import date

from rowing_plan.physiological_evidence import normalize_assessment_measurements
from rowing_plan.power_profile import profile_test_rejection_reason
from rowing_plan.performance_profile import build_athlete_performance_profile
from rowing_plan.validators import validate_profile


def _profile(row):
    return {"season":{"start_date":"2026-09-01","end_date":"2026-12-01"},"tests":{"testing_blocks":[{"id":"winter","label":"Winter testing episode","performance_tests":[row]}]}}


def test_r20_distance_is_valid_and_preserves_fixed_duration_and_rate_metadata():
    row={"id":"r20","protocol":"thirty_min_rate_capped_optional","test_date":"2026-11-27","distance_meters":6588,"duration_seconds":1800,"rate_cap_spm":20,"effort_intent":"submaximal","valid_for_profile":True}
    assert validate_profile(_profile(row)) == []
    measurement=normalize_assessment_measurements(_profile(row))[0]
    assert measurement.outcomes[0].key == "distance_meters" and measurement.outcomes[0].unit == "m"
    assert measurement.duration_seconds == 1800


def test_misclassified_6588_watt_r20_is_rejected_and_excluded_from_power_evidence():
    row={"id":"bad-r20","protocol":"thirty_min_rate_capped_optional","test_date":"2026-11-27","average_watts":6588,"duration_seconds":1800,"rate_cap_spm":20,"valid_for_profile":True}
    assert any("review range" in error for error in validate_profile(_profile(row)))
    assert profile_test_rejection_reason(row,date(2026,11,27),365) == "outside the app's data-entry review range; check measurement type and units"
    performance=build_athlete_performance_profile(_profile(row),as_of=date(2026,11,27))
    assert performance.thirty_r20_watts is None


def test_legacy_seven_stroke_average_watts_remains_supported():
    row={"id":"seven","protocol":"seven_stroke_peak","average_watts":287,"valid_for_profile":True}
    assert validate_profile(_profile(row)) == []
