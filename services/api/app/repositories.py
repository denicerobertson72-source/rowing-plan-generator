"""Persistence boundaries with a small relational development implementation."""
from __future__ import annotations
import json
import os
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

class PostgresRepositories:
    """Supabase Postgres store. Selected only when SUPABASE_DB_URL is configured."""
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg  # noqa: F401
        except ImportError as error:
            raise RuntimeError("psycopg is required when SUPABASE_DB_URL is set") from error
        self.database_url = database_url
        self._initialize()
    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.database_url, row_factory=dict_row)
    def _initialize(self) -> None:
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("""
              CREATE TABLE IF NOT EXISTS athletes (
                athlete_id TEXT PRIMARY KEY, user_id TEXT, profile_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
              );
              CREATE TABLE IF NOT EXISTS plan_versions (
                plan_id TEXT PRIMARY KEY, athlete_id TEXT NOT NULL REFERENCES athletes(athlete_id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL, plan_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (athlete_id, version_number)
              );
              CREATE TABLE IF NOT EXISTS workout_logs (
                log_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan_versions(plan_id) ON DELETE CASCADE,
                session_key TEXT NOT NULL, payload_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
              );
              CREATE INDEX IF NOT EXISTS workout_logs_plan_created_idx ON workout_logs (plan_id, created_at DESC);
            """)
    def create(self, profile: dict[str, Any], user_id: str | None = None) -> str:
        from psycopg.types.json import Jsonb
        athlete_id=str(uuid4())
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("INSERT INTO athletes (athlete_id, user_id, profile_json) VALUES (%s, %s, %s)",(athlete_id,user_id,Jsonb(profile)))
        return athlete_id
    def save(self, athlete_id: str, profile: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("UPDATE athletes SET profile_json=%s, updated_at=NOW() WHERE athlete_id=%s",(Jsonb(profile),athlete_id))
    def get(self, athlete_id: str) -> dict[str, Any] | None:
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT profile_json FROM athletes WHERE athlete_id=%s",(athlete_id,)); row=cursor.fetchone()
        return row["profile_json"] if row else None
    def save_plan(self, athlete_id: str, plan: dict[str, Any]) -> str:
        from psycopg.types.json import Jsonb
        plan_id=str(uuid4())
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(athlete_id,))
            cursor.execute("SELECT COALESCE(MAX(version_number),0)+1 AS version_number FROM plan_versions WHERE athlete_id=%s",(athlete_id,))
            version=cursor.fetchone()["version_number"]
            cursor.execute("INSERT INTO plan_versions (plan_id, athlete_id, version_number, plan_json) VALUES (%s, %s, %s, %s)",(plan_id,athlete_id,version,Jsonb(plan)))
        return plan_id
    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT plan_id, athlete_id, version_number, created_at, plan_json FROM plan_versions WHERE plan_id=%s",(plan_id,)); row=cursor.fetchone()
        return {"plan_id":row["plan_id"],"athlete_id":row["athlete_id"],"version_number":row["version_number"],"created_at":row["created_at"].isoformat(),"plan":row["plan_json"]} if row else None
    def save_log(self, plan_id: str, session_key: str, payload: dict[str, Any]) -> str:
        from psycopg.types.json import Jsonb
        log_id=str(uuid4())
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("INSERT INTO workout_logs (log_id, plan_id, session_key, payload_json) VALUES (%s, %s, %s, %s)",(log_id,plan_id,session_key,Jsonb(payload)))
        return log_id
    def logs_for_plan(self, plan_id: str) -> list[dict[str, Any]]:
        with self._connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT log_id, session_key, created_at, payload_json FROM workout_logs WHERE plan_id=%s ORDER BY created_at DESC",(plan_id,)); rows=cursor.fetchall()
        return [{"log_id":row["log_id"],"session_key":row["session_key"],"created_at":row["created_at"].isoformat(),"payload":row["payload_json"]} for row in rows]

class LazyRepositories:
    """Avoid opening a database connection during a serverless function import."""
    def __init__(self) -> None:
        self._instance: SQLiteRepositories | PostgresRepositories | None = None
    def _get(self) -> SQLiteRepositories | PostgresRepositories:
        if self._instance is None:
            database_url=os.environ.get("SUPABASE_DB_URL")
            self._instance=PostgresRepositories(database_url) if database_url else SQLiteRepositories(Path(__file__).resolve().parents[1] / "data" / "rowing_plan.sqlite3")
        return self._instance
    def __getattr__(self, name: str):
        return getattr(self._get(), name)

REPOSITORIES = LazyRepositories()
