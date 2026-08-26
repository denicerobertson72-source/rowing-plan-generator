from rowing_plan.schedule_scoring import choose

def test_one_flexible_session_chooses_one_allowed_day():
    result=choose({"sessions_per_week":1,"scheduling_status":"flexible","allowed_days":["tuesday","thursday"],"preferred_days":[],"prohibited_days":[]},{"tuesday"},set())
    assert result["scheduled_days"] == ["thursday"]

def test_fixed_activity_does_not_move():
    result=choose({"sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"]},{"wednesday"},set())
    assert result["scheduled_days"] == ["wednesday"]

def test_two_flexible_coached_rows_choose_two_distinct_allowed_days():
    result=choose({"sessions_per_week":2,"scheduling_status":"flexible","allowed_days":["monday","wednesday","friday"],"preferred_days":[],"prohibited_days":[]},{"monday"},set())
    assert len(result["scheduled_days"]) == 2
    assert "monday" not in result["scheduled_days"]
