import sys
import types

import pytest

from services.api.app.repositories import DiagnosticPostgresReadRepository


def test_diagnostic_postgres_repository_uses_short_non_session_configured_connections(monkeypatch):
    captured = {}

    def connect(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "psycopg.rows", types.SimpleNamespace(dict_row=object()))
    repository = DiagnosticPostgresReadRepository("postgresql://diagnostic-only")
    repository._connect()

    assert captured["url"] == "postgresql://diagnostic-only"
    assert captured["connect_timeout"] == 5
    assert captured["autocommit"] is True
    assert "options" not in captured


class _Cursor:
    def __init__(self, rows=(), fail_select=False):
        self.rows = list(rows)
        self.fail_select = fail_select
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, parameters=None):
        self.executed.append((statement, parameters))
        if self.fail_select and statement.startswith("SELECT"):
            raise RuntimeError("read_failed")

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def cursor(self):
        return self._cursor


@pytest.mark.parametrize(
    ("method", "argument", "rows"),
    (
        ("get", "athlete-1", [{"profile_json": {"athlete": {}}}]),
        ("athlete_owner", "athlete-1", [{"user_id": "user-1"}]),
        ("latest_plan_for_athlete", "athlete-1", []),
        ("logs_for_plan", "plan-1", []),
        ("weekly_overrides", "athlete-1", []),
    ),
)
def test_every_diagnostic_read_uses_operation_scoped_read_only_transaction(monkeypatch, method, argument, rows):
    cursor = _Cursor(rows)
    connection = _Connection(cursor)
    repository = DiagnosticPostgresReadRepository("postgresql://diagnostic-only")
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    getattr(repository, method)(argument)

    assert cursor.executed[0] == ("BEGIN TRANSACTION READ ONLY", None)
    assert cursor.executed[1][0].startswith("SELECT")
    assert cursor.executed[-1] == ("ROLLBACK", None)
    assert connection.closed is True


def test_diagnostic_read_rolls_back_after_select_failure(monkeypatch):
    cursor = _Cursor(fail_select=True)
    connection = _Connection(cursor)
    repository = DiagnosticPostgresReadRepository("postgresql://diagnostic-only")
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    with pytest.raises(RuntimeError, match="read_failed"):
        repository.get("athlete-1")

    assert cursor.executed[-1] == ("ROLLBACK", None)
    assert connection.closed is True


def test_diagnostic_postgres_repository_rejects_non_select_statements():
    class Cursor:
        def execute(self, statement, parameters):
            assert statement == "SELECT 1"
            assert parameters == ()

        def fetchall(self):
            return []

    cursor = Cursor()
    assert DiagnosticPostgresReadRepository._select(cursor, "SELECT 1", ()) == []
    for statement in ("INSERT INTO athletes VALUES (1)", "UPDATE athletes SET user_id='x'", "DELETE FROM athletes", "CREATE TABLE forbidden (id int)", "ALTER TABLE athletes ADD COLUMN forbidden int", "DROP TABLE athletes"):
        with pytest.raises(RuntimeError, match="diagnostic_read_repository_select_only"):
            DiagnosticPostgresReadRepository._select(cursor, statement, ())
