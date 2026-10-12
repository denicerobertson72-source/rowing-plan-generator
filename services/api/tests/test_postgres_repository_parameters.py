from contextlib import contextmanager
from datetime import datetime

from services.api.app.repositories import PostgresRepositories


class _Cursor:
    def __init__(self): self.calls=[]; self.rows=[{"athlete_id":"athlete-1","profile_json":{"athlete":{} }}, {"plan_id":"plan-1"}]
    def execute(self, query, parameters=None):
        self.calls.append((query, parameters))
        if "plan_versions" in query:
            assert parameters == ("athlete-1",)
    def fetchone(self): return self.rows.pop(0)

class _Connection:
    def __init__(self, cursor): self._cursor=cursor
    @contextmanager
    def cursor(self): yield self._cursor
    def __enter__(self): return self
    def __exit__(self, *_): return False

def test_latest_for_user_binds_string_athlete_id_as_one_element_tuple(monkeypatch):
    repository=object.__new__(PostgresRepositories)
    cursor=_Cursor()
    monkeypatch.setattr(repository, "_connect", lambda: _Connection(cursor))
    result=repository.latest_for_user("user-1")
    assert result["athlete_id"]=="athlete-1" and result["plan_id"]=="plan-1"
    assert cursor.calls[1][1] == ("athlete-1",)
