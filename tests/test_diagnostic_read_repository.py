import sys
import types

import pytest

from services.api.app.repositories import DiagnosticPostgresReadRepository


def test_diagnostic_postgres_repository_uses_database_enforced_read_only_connections(monkeypatch):
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
    assert captured["options"] == "-c default_transaction_read_only=on"


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
