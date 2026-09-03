import httpx
from services.api.tests.disposable_browser_runner import running_disposable_api

def test_runner_starts_real_api_against_disposable_database():
    with running_disposable_api() as run:
        assert httpx.get(f'{run["api_base"]}/health').json()["status"] == "ok"
        athlete=httpx.get(f'{run["api_base"]}/athletes/{run["athlete_id"]}').json()
        assert [race["start_date"] for race in athlete["athlete_profile"]["races"]] == ["2026-09-26","2026-10-17","2026-11-07"]
