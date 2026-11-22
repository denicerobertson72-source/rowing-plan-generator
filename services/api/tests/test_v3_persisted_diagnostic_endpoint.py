from datetime import date

from fastapi.testclient import TestClient

from services.api.app import main
from services.api.app.main import app


class _ReadOnlyDiagnosticRepository:
    instances = []

    def __init__(self, database_url):
        self.database_url = database_url
        self.calls = []
        type(self).instances.append(self)

    def get(self, athlete_id):
        self.calls.append(("get", athlete_id))
        return {"athlete": {"display_name": "Diagnostic athlete"}}

    def athlete_owner(self, athlete_id):
        self.calls.append(("athlete_owner", athlete_id))
        return "development-user"

    def latest_plan_for_athlete(self, athlete_id):
        self.calls.append(("latest_plan_for_athlete", athlete_id))
        return None

    def logs_for_plan(self, plan_id):
        self.calls.append(("logs_for_plan", plan_id))
        return []

    def weekly_overrides(self, athlete_id):
        self.calls.append(("weekly_overrides", athlete_id))
        return []

    def __getattr__(self, name):
        raise AssertionError(f"unexpected_diagnostic_repository_method:{name}")


def test_persisted_v3_diagnostic_requires_existing_auth(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")
    assert response.status_code == 401


def test_persisted_v3_diagnostic_rejects_wrong_athlete_owner(monkeypatch):
    class WrongOwner(_ReadOnlyDiagnosticRepository):
        def athlete_owner(self, athlete_id):
            self.calls.append(("athlete_owner", athlete_id))
            return "someone-else"

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", WrongOwner)
    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")
    assert response.status_code == 403
    assert "belongs to another account" in response.json()["detail"]


def test_persisted_v3_diagnostic_requires_live_allowlist(monkeypatch):
    _ReadOnlyDiagnosticRepository.instances.clear()
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "another-athlete")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")
    assert response.status_code == 403
    assert "not enabled" in response.json()["detail"]


def test_persisted_v3_diagnostic_requires_database_url_without_fallback(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_persisted_v3_diagnostic_uses_only_read_repository_and_wrapper(monkeypatch):
    _ReadOnlyDiagnosticRepository.instances.clear()
    expected = {"human_readable": "R9A report", "conservation": {"pass": True}}
    wrapper_calls = []

    def no_normal_postgres_initialization(*args, **kwargs):
        raise AssertionError("normal_postgres_repositories_must_not_initialize")

    def diagnostic_wrapper(repository, athlete_id, *, planning_date):
        wrapper_calls.append((repository, athlete_id, planning_date))
        return expected

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    monkeypatch.setattr(main, "generate_persisted_v3_diagnostic_report", diagnostic_wrapper)
    monkeypatch.setattr("services.api.app.repositories.PostgresRepositories.__init__", no_normal_postgres_initialization)
    monkeypatch.setattr(main, "current_planning_date", lambda: date(2026, 11, 22))

    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")

    assert response.status_code == 200
    assert response.json() == expected
    repository = _ReadOnlyDiagnosticRepository.instances[-1]
    assert repository.database_url == "postgresql://configured"
    assert repository.calls == [("get", "athlete-1"), ("athlete_owner", "athlete-1")]
    assert wrapper_calls == [(repository, "athlete-1", date(2026, 11, 22))]
