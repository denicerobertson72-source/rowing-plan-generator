"""One-command Step 6B E2E lifecycle: temporary DB, API, Next, Playwright, cleanup."""
from __future__ import annotations
import os, subprocess, sys, time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT)); WEB=ROOT/"apps/web"; DEPS="/private/tmp/rowing-plan-api-test-deps"
from services.api.tests.disposable_browser_runner import running_disposable_api
def main() -> int:
  os.chdir(ROOT)
  with running_disposable_api(8011) as run:
    env=os.environ.copy(); env["NEXT_PUBLIC_API_BASE_URL"]=run["api_base"]
    web=subprocess.Popen(["npm","run","dev","--","--port","3101"],cwd=WEB,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
      for _ in range(150):
        try:
          if httpx.get("http://127.0.0.1:3101/profile",timeout=.5).is_success: break
        except httpx.HTTPError: pass
        time.sleep(.1)
      else: raise RuntimeError("E2E web server did not start")
      return subprocess.run(["npx","playwright","test"],cwd=WEB,env=env).returncode
    finally:
      web.terminate()
      try: web.wait(timeout=5)
      except subprocess.TimeoutExpired: web.kill()
if __name__ == "__main__": raise SystemExit(main())
