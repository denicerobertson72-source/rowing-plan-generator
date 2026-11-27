"""Run only the mocked Supabase-refresh browser regression against disposable services."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"
sys.path.insert(0, str(ROOT))
from services.api.tests.disposable_browser_runner import running_disposable_api


def main() -> int:
    os.chdir(ROOT)
    previous_origins = os.environ.get("ALLOWED_ORIGINS")
    os.environ["ALLOWED_ORIGINS"] = "http://127.0.0.1:3102"
    with running_disposable_api(8012) as run:
        env = os.environ.copy()
        env.update({
            "NEXT_PUBLIC_API_BASE_URL": run["api_base"],
            # Public test values enable the browser client only. Playwright
            # intercepts the refresh exchange; it never leaves this harness.
            "NEXT_PUBLIC_SUPABASE_URL": "https://diagnostic-auth.supabase.co",
            "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "disposable-browser-test-key",
            "PLAYWRIGHT_BASE_URL": "http://127.0.0.1:3102",
        })
        web = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", "3102"],
            cwd=WEB, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(150):
                try:
                    if httpx.get("http://127.0.0.1:3102/developer-diagnostics", timeout=.5).is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Auth-refresh E2E web server did not start")
            return subprocess.run(
                ["npx", "playwright", "test", "-g", "Developer Diagnostics.*refresh"],
                cwd=WEB, env=env,
            ).returncode
        finally:
            web.terminate()
            try:
                web.wait(timeout=5)
            except subprocess.TimeoutExpired:
                web.kill()
            if previous_origins is None:
                os.environ.pop("ALLOWED_ORIGINS", None)
            else:
                os.environ["ALLOWED_ORIGINS"] = previous_origins


if __name__ == "__main__":
    raise SystemExit(main())
