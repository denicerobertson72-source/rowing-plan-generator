from collections import Counter

from rowing_plan.session_archetypes import SessionArchetypeValidator, build_archetype_library, developer_report, eligible_archetypes


def test_catalog_is_valid_and_has_required_structural_choice():
    library = build_archetype_library()
    assert not SessionArchetypeValidator().validate(library)
    counts = Counter(item["primary_band"] for item in library)
    assert all(counts[band] >= minimum for band, minimum in {"UT3": 5, "UT2": 8, "UT1": 6, "AT": 6, "TR": 6, "AN": 5, "PP": 5}.items())
    assert len({item["structure_family"] for item in library if item["primary_band"] == "UT2"}) >= 6
    assert len({item["structure_family"] for item in library if item["primary_band"] == "AT"}) >= 5
    assert len({item["structure_family"] for item in library if item["primary_band"] in {"TR", "AN", "PP"}}) >= 8


def test_experience_race_environment_and_recovery_safeguards():
    novice = eligible_archetypes(experience="novice", role="aerobic_base")
    assert len(novice) >= 8 and not any(item["primary_band"] in {"AT", "TR", "AN", "PP"} for item in novice)
    assert eligible_archetypes(experience="competitive", band="AN")
    assert len(eligible_archetypes(experience="experienced", race_type="head_5k")) >= 6
    assert len(eligible_archetypes(experience="experienced", race_type="erg_2k")) >= 6
    assert len(eligible_archetypes(experience="competitive", race_type="sprint_1k")) >= 6
    assert eligible_archetypes(experience="experienced", environment="water")
    assert eligible_archetypes(experience="experienced", environment="erg")
    for item in build_archetype_library():
        if item["load_classification"] in {"high", "very_high"}:
            assert item["minimum_recovery_guidance"] and item["requires_easy_day_after"]


def test_preferences_non_rowing_accounting_provenance_and_report():
    aerobic = eligible_archetypes(experience="experienced", role="aerobic_base")
    assert any(item["preference_fit"]["shorter_pieces"] == "poor_fit" for item in aerobic)
    assert any(item["preference_fit"]["longer_pieces"] != "poor_fit" for item in aerobic)
    alternate = [item for item in build_archetype_library() if item["session_role"] == "alternate_aerobic"]
    strength = [item for item in build_archetype_library() if item["session_role"] == "strength"]
    assert alternate and strength and all(item["primary_band"] is None for item in alternate + strength)
    assert all(item["source_ids"] and item["original_app_wording"] for item in build_archetype_library())
    report = developer_report()
    assert "| ID | Name | Band | Role |" in report and "Work range (min)" in report and "ut2_01" in report
    assert len(eligible_archetypes(experience="experienced", role="LONG_AEROBIC", band="UT2")) >= 8
    assert len(eligible_archetypes(experience="novice", role="AEROBIC_BASE")) >= 8
    assert len(eligible_archetypes(experience="experienced", race_type="head_5k")) >= 6
    assert len(eligible_archetypes(experience="competitive", race_type="sprint_1k")) >= 6
