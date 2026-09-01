"""Select only app-owned templates, never free-form workout text."""
from __future__ import annotations
import json
from pathlib import Path

def load_library(path: str | Path = "data/session_library.json") -> list[dict]:
    return json.loads(Path(path).read_text())["sessions"]

def select_session(library: list[dict], band: str, phase: str, race_type: str, modes: list[str], minutes: int, structure_preference: str = "varied") -> dict | None:
    candidates=[]
    for s in library:
        if band not in s["bands"]: continue
        if "all" not in s["phase_tags"] and phase not in s["phase_tags"]: continue
        if "all" not in s["race_type_tags"] and race_type not in s["race_type_tags"]: continue
        if not set(modes).intersection(s["modes"]): continue
        low, high=s["total_minutes_range"]
        if low <= minutes <= high: candidates.append(s)
    if not candidates: return None
    # This only breaks ties between equivalent band/phase/mode candidates; it
    # does not alter training band, duration, race timing, or load.
    def structure_rank(item: dict) -> tuple[int,str]:
        text=item.get("work_structure","").lower()
        if structure_preference=="long_intervals": return (0 if "minutes" in text and "seconds" not in text else 1,item["session_id"])
        if structure_preference=="short_intervals": return (0 if "seconds" in text or "strokes" in text else 1,item["session_id"])
        if structure_preference=="repeatable": return (0,item["session_id"])
        return (0,item["session_id"])
    return sorted(candidates, key=structure_rank)[0]
