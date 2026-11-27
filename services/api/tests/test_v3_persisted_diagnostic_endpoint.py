from datetime import date
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from services.api.app import main
from services.api.app.main import app


PRODUCTION_PWA_ORIGIN = "https://rowing-plan-pwa.vercel.app"


@pytest.fixture(autouse=True)
def _developer_diagnostic_access(monkeypatch):
    """Existing endpoint cases exercise an explicitly authorized developer."""
    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_USER_IDS", "development-user")


class _ReadOnlyDiagnosticRepository:
    instances = []

    def __init__(self, database_url, *, diagnostic_reference=None):
        self.database_url = database_url
        self.diagnostic_reference = diagnostic_reference
        self.calls = []
        self.transaction_count = 0
        type(self).instances.append(self)

    @contextmanager
    def read_transaction(self, *, checkpoint=None):
        self.transaction_count += 1
        if checkpoint is not None:
            checkpoint("db_connect_complete", 1.0)
            checkpoint("readonly_begin_start", 0.0)
            checkpoint("readonly_begin_complete", 1.0)
        try:
            yield self
        finally:
            if checkpoint is not None:
                checkpoint("rollback_start", 0.0)
                checkpoint("rollback_complete", 1.0)

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


def test_developer_diagnostics_access_status_is_authenticated_but_non_sensitive(monkeypatch):
    app.dependency_overrides[main.current_user_id] = lambda: "development-user"
    try:
        response = TestClient(app).get("/api/v1/account/developer-diagnostics-access")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"developer_diagnostics_authorized": True}


def test_developer_diagnostics_access_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.delenv("DEVELOPER_DIAGNOSTIC_USER_IDS", raising=False)
    app.dependency_overrides[main.current_user_id] = lambda: "development-user"
    try:
        response = TestClient(app).get("/api/v1/account/developer-diagnostics-access")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"developer_diagnostics_authorized": False}


def test_persisted_v3_diagnostic_requires_explicit_developer_access(monkeypatch):
    _ReadOnlyDiagnosticRepository.instances.clear()
    monkeypatch.delenv("DEVELOPER_DIAGNOSTIC_USER_IDS", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")
    assert response.status_code == 403
    assert response.json()["detail"] == "Developer diagnostics access is required."
    assert not _ReadOnlyDiagnosticRepository.instances


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


def test_persisted_v3_diagnostic_uses_only_read_repository_and_wrapper(monkeypatch, caplog):
    _ReadOnlyDiagnosticRepository.instances.clear()
    caplog.set_level("INFO", logger="services.api.app.main")
    expected = {"human_readable": "R9A report", "conservation": {"pass": True}}
    wrapper_calls = []

    def no_normal_postgres_initialization(*args, **kwargs):
        raise AssertionError("normal_postgres_repositories_must_not_initialize")

    def diagnostic_wrapper(repository, athlete_id, *, planning_date, profile=None, stage_timings_ms=None, stage_checkpoint=None):
        wrapper_calls.append((repository, athlete_id, planning_date, profile))
        if stage_timings_ms is not None:
            stage_timings_ms["persisted_snapshot_load"] = 1.0
            stage_timings_ms["report_generation"] = 2.0
        if stage_checkpoint is not None:
            stage_checkpoint("snapshot_complete", 1.0)
            stage_checkpoint("report_start", 0.0)
            stage_checkpoint("report_complete", 2.0)
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
    assert "no-store" in response.headers["cache-control"]
    repository = _ReadOnlyDiagnosticRepository.instances[-1]
    assert repository.database_url == "postgresql://configured"
    assert repository.calls == [("get", "athlete-1"), ("athlete_owner", "athlete-1")]
    assert repository.transaction_count == 1
    assert wrapper_calls == [(repository, "athlete-1", date(2026, 11, 22), {"athlete": {"display_name": "Diagnostic athlete"}})]
    assert "persisted_v3_checkpoint reference=r9a_" in caplog.text
    assert "stage=request_start" in caplog.text
    assert "stage=request_complete" in caplog.text
    assert "Diagnostic athlete" not in caplog.text


def test_persisted_v3_diagnostic_checkpoint_order_is_immediate_and_sanitized(monkeypatch, caplog):
    _ReadOnlyDiagnosticRepository.instances.clear()
    caplog.set_level("INFO", logger="services.api.app.main")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)

    def diagnostic_wrapper(*args, stage_checkpoint=None, **kwargs):
        stage_checkpoint("snapshot_complete", 2.0)
        stage_checkpoint("report_start", 0.0)
        stage_checkpoint("report_complete", 3.0)
        return {"human_readable": "R9A report"}

    monkeypatch.setattr(main, "generate_persisted_v3_diagnostic_report", diagnostic_wrapper)
    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")

    assert response.status_code == 200
    checkpoints = [record.message for record in caplog.records if record.message.startswith("persisted_v3_checkpoint")]
    stages = [entry.split("stage=", 1)[1].split()[0] for entry in checkpoints]
    assert stages == [
        "request_start", "db_connect_start", "db_connect_complete",
        "readonly_begin_start", "readonly_begin_complete", "ownership_start",
        "ownership_complete", "snapshot_start", "snapshot_complete", "report_start",
        "report_complete", "rollback_start", "rollback_complete", "request_complete",
    ]
    assert all("configured" not in entry and "Diagnostic athlete" not in entry for entry in checkpoints)


def _production_pwa_client():
    """Temporarily exercise the actual app middleware with the deployed PWA origin."""
    cors = next(item for item in app.user_middleware if item.cls.__name__ == "CORSMiddleware")
    prior_origins = cors.kwargs["allow_origins"]
    cors.kwargs["allow_origins"] = [PRODUCTION_PWA_ORIGIN]
    app.middleware_stack = None
    return TestClient(app, raise_server_exceptions=False), cors, prior_origins


def test_persisted_v3_diagnostic_accepts_configured_production_pwa_origin_and_preflight(monkeypatch):
    _ReadOnlyDiagnosticRepository.instances.clear()
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    monkeypatch.setattr(main, "generate_persisted_v3_diagnostic_report", lambda *args, **kwargs: {"human_readable": "R9A report"})
    app.dependency_overrides[main.current_user_id] = lambda: "development-user"
    client, cors, prior_origins = _production_pwa_client()
    try:
        preflight = client.options(
            "/api/v1/athletes/athlete-1/diagnostics/v3-persisted",
            headers={
                "Origin": PRODUCTION_PWA_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        response = client.get(
            "/api/v1/athletes/athlete-1/diagnostics/v3-persisted",
            headers={"Origin": PRODUCTION_PWA_ORIGIN, "Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()
        cors.kwargs["allow_origins"] = prior_origins
        app.middleware_stack = None

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == PRODUCTION_PWA_ORIGIN
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == PRODUCTION_PWA_ORIGIN


def test_persisted_v3_diagnostic_internal_failure_is_safe_and_keeps_cors_headers(monkeypatch, caplog):
    _ReadOnlyDiagnosticRepository.instances.clear()
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    monkeypatch.setattr(
        main,
        "generate_persisted_v3_diagnostic_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("postgresql://secret@example.invalid/diagnostic_failure")),
    )
    app.dependency_overrides[main.current_user_id] = lambda: "development-user"
    client, cors, prior_origins = _production_pwa_client()
    try:
        response = client.get(
            "/api/v1/athletes/athlete-1/diagnostics/v3-persisted",
            headers={"Origin": PRODUCTION_PWA_ORIGIN, "Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()
        cors.kwargs["allow_origins"] = prior_origins
        app.middleware_stack = None

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == PRODUCTION_PWA_ORIGIN
    detail = response.json()["detail"]
    assert detail["error_code"] == "persisted_v3_diagnostic_failed"
    assert detail["failure_stage"] == "persisted_report_generation"
    assert detail["error_id"].startswith("r9a_")
    assert "secret" not in response.text
    assert "persisted_v3_diagnostic_failed" in caplog.text
    assert "secret" not in caplog.text


def test_persisted_v3_diagnostic_connection_failure_is_sanitized(monkeypatch, caplog):
    class TimedOutReadRepository(_ReadOnlyDiagnosticRepository):
        def get(self, athlete_id):
            raise TimeoutError("postgresql://secret@example.invalid/connect_timeout")

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", TimedOutReadRepository)
    app.dependency_overrides[main.current_user_id] = lambda: "development-user"
    client, cors, prior_origins = _production_pwa_client()
    try:
        response = client.get(
            "/api/v1/athletes/athlete-1/diagnostics/v3-persisted",
            headers={"Origin": PRODUCTION_PWA_ORIGIN, "Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()
        cors.kwargs["allow_origins"] = prior_origins
        app.middleware_stack = None

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == PRODUCTION_PWA_ORIGIN
    detail = response.json()["detail"]
    assert detail["error_code"] == "persisted_v3_diagnostic_failed"
    assert detail["failure_stage"] == "ownership_lookup"
    assert detail["error_id"].startswith("r9a_")
    assert "secret" not in response.text
    assert "secret" not in caplog.text


def test_persisted_v3_diagnostic_transaction_entry_failure_is_sanitized(monkeypatch, caplog):
    class FailingTransactionRepository(_ReadOnlyDiagnosticRepository):
        @contextmanager
        def read_transaction(self, *, checkpoint=None):
            raise TimeoutError("postgresql://secret@example.invalid/connect_timeout")
            yield self

    caplog.set_level("ERROR", logger="services.api.app.main")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", FailingTransactionRepository)
    app.dependency_overrides[main.current_user_id] = lambda: "development-user"
    client, cors, prior_origins = _production_pwa_client()
    try:
        response = client.get(
            "/api/v1/athletes/athlete-1/diagnostics/v3-persisted",
            headers={"Origin": PRODUCTION_PWA_ORIGIN, "Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()
        cors.kwargs["allow_origins"] = prior_origins
        app.middleware_stack = None

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == PRODUCTION_PWA_ORIGIN
    detail = response.json()["detail"]
    assert detail["error_code"] == "persisted_v3_diagnostic_failed"
    assert detail["failure_stage"] == "diagnostic_db_connect"
    assert detail["error_id"].startswith("r9a_")
    assert "secret" not in response.text
    assert "secret" not in caplog.text


def test_persisted_v3_diagnostic_serializes_a_public_payload_and_excludes_raw_planner_results(monkeypatch):
    _ReadOnlyDiagnosticRepository.instances.clear()
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    raw_r4 = object()
    raw_r5 = object()
    monkeypatch.setattr(
        main,
        "generate_persisted_v3_diagnostic_report",
        lambda *args, **kwargs: {
            "calendar": [{"date": "2026-11-16", "components": []}],
            "workouts": [{"component_id": "component-1", "status": "success"}],
            "conservation": {"pass": True}, "human_readable": "R9A report",
            "r4": raw_r4, "r5": raw_r5,
        },
    )

    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content.startswith(b"{")
    payload = response.json()
    assert payload["calendar"][0]["date"] == "2026-11-16"
    assert payload["workouts"][0]["component_id"] == "component-1"
    assert "r4" not in payload
    assert "r5" not in payload


def test_persisted_v3_diagnostic_serialization_failure_is_sanitized(monkeypatch, caplog):
    class Unserializable:
        __slots__ = ()

    caplog.set_level("ERROR", logger="services.api.app.main")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://configured")
    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", "athlete-1")
    monkeypatch.setattr(main, "DiagnosticPostgresReadRepository", _ReadOnlyDiagnosticRepository)
    monkeypatch.setattr(
        main,
        "generate_persisted_v3_diagnostic_report",
        lambda *args, **kwargs: {"current_training": Unserializable(), "human_readable": "R9A report"},
    )

    response = TestClient(app).get("/api/v1/athletes/athlete-1/diagnostics/v3-persisted")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error_code"] == "persisted_v3_diagnostic_failed"
    assert detail["failure_stage"] == "response_serialization"
    assert detail["error_id"].startswith("r9a_")
    assert "Unserializable" not in response.text
    assert "Unserializable" not in caplog.text
    assert "failure_stage=response_serialization" in caplog.text
