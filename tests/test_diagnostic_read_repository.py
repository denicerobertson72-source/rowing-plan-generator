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


def test_one_read_transaction_reuses_one_connection_for_the_complete_diagnostic_snapshot(monkeypatch):
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = DiagnosticPostgresReadRepository("postgresql://diagnostic-only")
    connection_calls = []
    monkeypatch.setattr(repository, "_connect", lambda: (connection_calls.append(True), connection)[1])

    with repository.read_transaction() as transaction:
        transaction.get("athlete-1")
        transaction.athlete_owner("athlete-1")
        transaction.latest_plan_for_athlete("athlete-1")
        transaction.logs_for_plan("plan-1")
        transaction.weekly_overrides("athlete-1")

    assert len(connection_calls) == 1
    assert [statement for statement, _parameters in cursor.executed].count("BEGIN TRANSACTION READ ONLY") == 1
    assert sum(statement.startswith("SELECT") for statement, _parameters in cursor.executed) == 5
    assert [statement for statement, _parameters in cursor.executed].count("ROLLBACK") == 1
    assert connection.closed is True


def test_diagnostic_query_timings_are_sanitized_and_sequence_repeated_log_reads(monkeypatch, caplog):
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = DiagnosticPostgresReadRepository(
        "postgresql://secret@example.invalid/diagnostic",
        diagnostic_reference="r9a_query_test",
    )
    monkeypatch.setattr(repository, "_connect", lambda: connection)
    caplog.set_level("INFO", logger="services.api.app.repositories")

    with repository.read_transaction() as transaction:
        transaction.get("athlete-secret")
        transaction.athlete_owner("athlete-secret")
        transaction.latest_plan_for_athlete("athlete-secret")
        transaction.logs_for_plan("plan-secret")
        transaction.logs_for_plan("plan-secret-2")
        transaction.weekly_overrides("athlete-secret")

    messages = [record.message for record in caplog.records if record.message.startswith("persisted_v3_query")]
    assert len(messages) == 6
    assert "reference=r9a_query_test query=get sequence=1 elapsed_ms=" in messages[0]
    assert "query=athlete_owner sequence=1 elapsed_ms=" in messages[1]
    assert "query=latest_plan_for_athlete sequence=1 elapsed_ms=" in messages[2]
    assert "query=logs_for_plan sequence=1 elapsed_ms=" in messages[3]
    assert "query=logs_for_plan sequence=2 elapsed_ms=" in messages[4]
    assert "query=weekly_overrides sequence=1 elapsed_ms=" in messages[5]
    assert all("secret" not in message for message in messages)


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
