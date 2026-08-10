"""Persistence boundaries with a small relational development implementation."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

class AthleteRepository(Protocol):
    def create(self, profile: dict[str, Any], user_id: str | None = None) -> str: ...
    def save(self, athlete_id: str, profile: dict[str, Any]) -> None: ...
    def get(self, athlete_id: str) -> dict[str, Any] | None: ...

class PlanRepository(Protocol):
    def save(self, athlete_id: str, plan: dict[str, Any]) -> str: ...
    def get(self, plan_id: str) -> dict[str, Any] | None: ...

class TestRepository(Protocol): pass
class WorkoutLogRepository(Protocol): pass
class RaceRepository(Protocol): pass

class SQLiteRepositories:
    """Development SQLite store; replace implementation, not repository interfaces, in production."""
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS athletes (athlete_id TEXT PRIMARY KEY, user_id TEXT, profile_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS plan_versions (plan_id TEXT PRIMARY KEY, athlete_id TEXT NOT NULL, version_number INTEGER NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS workout_logs (log_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, session_key TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
    def _connect(self) -> sqlite3.Connection:
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; return db
    @staticmethod
    def _now() -> str: return datetime.now(timezone.utc).isoformat()
    def create(self, profile: dict[str, Any], user_id: str | None = None) -> str:
        athlete_id=str(uuid4()); now=self._now()
        with self._connect() as db: db.execute("INSERT INTO athletes VALUES (?, ?, ?, ?, ?)",(athlete_id,user_id,json.dumps(profile),now,now))
        return athlete_id
    def save(self, athlete_id: str, profile: dict[str, Any]) -> None:
        with self._connect() as db: db.execute("UPDATE athletes SET profile_json=?, updated_at=? WHERE athlete_id=?",(json.dumps(profile),self._now(),athlete_id))
    def get(self, athlete_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row=db.execute("SELECT profile_json FROM athletes WHERE athlete_id=?",(athlete_id,)).fetchone()
        return json.loads(row["profile_json"]) if row else None
    def save_plan(self, athlete_id: str, plan: dict[str, Any]) -> str:
        with self._connect() as db:
            version=db.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM plan_versions WHERE athlete_id=?",(athlete_id,)).fetchone()[0]
            plan_id=str(uuid4()); db.execute("INSERT INTO plan_versions VALUES (?, ?, ?, ?, ?)",(plan_id,athlete_id,version,json.dumps(plan),self._now()))
        return plan_id
    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row=db.execute("SELECT * FROM plan_versions WHERE plan_id=?",(plan_id,)).fetchone()
        return {"plan_id":row["plan_id"],"athlete_id":row["athlete_id"],"version_number":row["version_number"],"created_at":row["created_at"],"plan":json.loads(row["plan_json"])} if row else None
    def save_log(self, plan_id: str, session_key: str, payload: dict[str, Any]) -> str:
        log_id=str(uuid4())
        with self._connect() as db: db.execute("INSERT INTO workout_logs VALUES (?, ?, ?, ?, ?)",(log_id,plan_id,session_key,json.dumps(payload),self._now()))
        return log_id
    def logs_for_plan(self, plan_id: str) -> list[dict[str, Any]]:
        with self._connect() as db: rows=db.execute("SELECT * FROM workout_logs WHERE plan_id=? ORDER BY created_at DESC",(plan_id,)).fetchall()
        return [{"log_id":row["log_id"],"session_key":row["session_key"],"created_at":row["created_at"],"payload":json.loads(row["payload_json"])} for row in rows]

REPOSITORIES = SQLiteRepositories(Path(__file__).resolve().parents[1] / "data" / "rowing_plan.sqlite3")
