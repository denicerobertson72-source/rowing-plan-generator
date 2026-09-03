from rowing_plan.session_selection import select_and_instantiate


def test_selection_is_deterministic_varied_and_time_bounded():
    args=dict(role="AEROBIC_BASE",experience="experienced",phase="general_preparation",race_type="head_5k",mode="erg",minutes=50,preference="shorter_pieces")
    first=select_and_instantiate(**args,history=[])
    again=select_and_instantiate(**args,history=[])
    assert first["archetype"]["archetype_id"] == again["archetype"]["archetype_id"]
    assert first["total_minutes"] <= 50
    repeated=[{**first["fingerprint"]} for _ in range(3)]
    changed=select_and_instantiate(**args,history=repeated)
    assert changed["archetype"]["archetype_id"] != first["archetype"]["archetype_id"]


def test_novice_and_race_specific_selection_safeguards():
    novice=select_and_instantiate(role="AEROBIC_BASE",experience="novice",phase="foundation_orientation",race_type="general",mode="erg",minutes=45,preference="mixed",history=[])
    assert novice["archetype"]["novice_allowed"] and novice["archetype"]["primary_band"] == "UT2"
    race=select_and_instantiate(role="RACE_PACE",experience="experienced",phase="race_build",race_type="head_5k",mode="water",minutes=60,preference="mixed",history=[])
    assert race["archetype"]["session_role"] == "head_race"
