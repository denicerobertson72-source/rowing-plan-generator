"""Lifecycle support for Step 6B browser acceptance; never targets the developer DB."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from services.api.tests.disposable_browser_fixture import REAL_DB, disposable_browser_fixture

TEMP_DEPS=Path("/private/tmp/rowing-plan-api-test-deps")

@contextmanager
def running_disposable_api(port: int = 8011):
    with disposable_browser_fixture() as fixture:
        db=fixture["database"].resolve()
        if not db.exists() or db == REAL_DB or db.parent.resolve() == REAL_DB.parent.resolve():
            raise RuntimeError("Refusing to start browser acceptance API against a non-disposable database")
        env=os.environ.copy(); env["ROWING_PLAN_DB_PATH"]=str(db)
        env["PYTHONPATH"]=os.pathsep.join([str(TEMP_DEPS), str(Path.cwd()), env.get("PYTHONPATH","")])
        # Keep the same interpreter used by the caller, but make the bundled
        # API dependencies importable for the disposable subprocess.
        process=subprocess.Popen([sys.executable,"-m","uvicorn","services.api.app.main:app","--host","127.0.0.1","--port",str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/api/v1/health",timeout=.5).is_success: break
                except httpx.HTTPError: pass
                time.sleep(.1)
            else: raise RuntimeError("Disposable API did not become healthy")
            yield {**fixture,"api_base":f"http://127.0.0.1:{port}/api/v1","pid":process.pid}
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
