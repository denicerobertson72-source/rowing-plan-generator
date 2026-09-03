from rowing_plan.load_transformations import transform

def test_taper_preserves_band_and_reduces_quality_quantity():
    session={"session_id":"head_01","archetype_id":"head_01","session_role":"RACE_PACE","band":"TR","rowing_minutes":50,"total_cardio_minutes":50,"quality_minutes":50,"structure":"5 × 5 min"}
    result=transform(session,phase="taper_sharpen",race_priority="A")
    assert result["band"]=="TR" and result["rowing_minutes"]<50
    assert result["load_transformation"]["race_rate_preserved"]
    assert result["original_structure"]=="5 × 5 min"

def test_strength_priority_is_categorical_and_race_is_untouched():
    lift=transform({"session_id":"LIFT","title":"Heavy lifting"},phase="taper_sharpen",race_priority="A")
    assert lift["strength_state"]=="reduced-load"
    race={"session_id":"RACE","rowing_minutes":20}
    assert transform(race,phase="race_recovery")==race
