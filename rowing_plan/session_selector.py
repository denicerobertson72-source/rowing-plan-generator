"""Select only app-owned templates, never free-form workout text."""
from __future__ import annotations
import json
from pathlib import Path

def load_library(path: str | Path = "data/session_library.json") -> list[dict]:
    return json.loads(Path(path).read_text())["sessions"]

def select_session(library: list[dict], band: str, phase: str, race_type: str, modes: list[str], minutes: int) -> dict | None:
    candidates=[]
    for s in library:
        if band not in s["bands"]: continue
        if "all" not in s["phase_tags"] and phase not in s["phase_tags"]: continue
        if "all" not in s["race_type_tags"] and race_type not in s["race_type_tags"]: continue
        if not set(modes).intersection(s["modes"]): continue
        low, high=s["total_minutes_range"]
        if low <= minutes <= high: candidates.append(s)
    return sorted(candidates, key=lambda x:x["session_id"])[0] if candidates else None
