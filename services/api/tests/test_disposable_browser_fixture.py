from services.api.tests.disposable_browser_fixture import REAL_DB, disposable_browser_fixture
from services.api.app.repositories import REPOSITORIES

def test_disposable_browser_fixture_creates_real_generated_plan_and_cleans_up():
    with disposable_browser_fixture() as fixture:
        assert fixture["database"].resolve() != REAL_DB
        assert fixture["database"].exists()
        assert REPOSITORIES.get(fixture["athlete_id"])["races"][2]["end_date"] == "2026-11-08"
        assert REPOSITORIES.get_plan(fixture["plan_id"])["version_number"] == 1
    assert not fixture["database"].exists()
