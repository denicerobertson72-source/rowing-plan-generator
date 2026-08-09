"""JSON import/export helpers; no external persistence is required."""
from __future__ import annotations
import json
from datetime import date, datetime

def dump_profile(profile: dict) -> bytes: return json.dumps(profile, indent=2, sort_keys=True, default=str).encode()
def load_profile(raw: bytes | str) -> dict: return json.loads(raw.decode() if isinstance(raw,bytes) else raw)
def safe_filename(name: str, suffix: str = ".xlsx") -> str:
    clean="".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "rowing_plan"
    return clean[:80]+suffix
