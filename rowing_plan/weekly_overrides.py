"""Week-scoped schedule changes; never mutate the permanent profile implicitly."""
from __future__ import annotations
from datetime import date

def normalize(override: dict) -> dict:
    value=dict(override); value.setdefault("scope","this_week_only"); value.setdefault("changes",[])
    if value["scope"] not in ("this_week_only","make_normal_schedule"): raise ValueError("Invalid override scope")
    date.fromisoformat(value["week_start"])
    return value

def apply_to_sessions(sessions: list[dict], override: dict) -> list[dict]:
    """Temporary availability/rest changes affect only matching dates; permanent profile is untouched."""
    ov=normalize(override); changes={c["date"]:c for c in ov["changes"]}
    out=[]
    for session in sessions:
        change=changes.get(session["date"])
        if change and change.get("unavailable") and not session.get("fixed"): continue
        out.append({**session,**({"weekly_override_applied":True,"override_reason":change.get("reason","Temporary schedule change")} if change else {})})
    return out
