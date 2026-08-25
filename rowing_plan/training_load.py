"""Descriptive session-RPE load; deliberately separate from HR-zone minutes."""
from collections import defaultdict
from datetime import date, timedelta
def session_load_au(payload):
    if payload.get("status")=="skipped": return 0
    return int(payload["actual_duration_min"]*payload["rpe"]) if payload.get("actual_duration_min") is not None and payload.get("rpe") is not None else None
def load_summary(logs):
    daily=defaultdict(int)
    for log in logs:
        value=session_load_au(log.get("payload",{})); day=log.get("session_key","").split(":",1)[0]
        if value is not None:
            try: date.fromisoformat(day); daily[day]+=value
            except ValueError: pass
    latest=max((date.fromisoformat(day) for day in daily),default=None)
    rolling=lambda n: sum(v for day,v in daily.items() if latest and latest-timedelta(days=n-1)<=date.fromisoformat(day)<=latest)
    return {"unit":"AU","formula":"actual duration minutes × session RPE (0–10)","daily_totals":[{"date":d,"load_au":v} for d,v in sorted(daily.items())],"rolling_7_day_au":rolling(7),"rolling_28_day_au":rolling(28),"interpretation":"Descriptive personal load only; not a zone-minute total, injury score, or universal safety threshold."}
