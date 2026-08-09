"""Small typed model helpers; public plans deliberately remain JSON dictionaries."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

@dataclass(frozen=True)
class Band:
    name: str
    domain: str
    hr_low: int | None = None
    hr_high: int | None = None
    watts_low: float | None = None
    watts_high: float | None = None
    spm_low: int | None = None
    spm_high: int | None = None
    effort_low: float | None = None
    effort_high: float | None = None
    method: str = "hrr_fallback"
    confidence: str = "low"
    assumptions: list[str] | None = None
    def to_dict(self) -> dict[str, Any]: return asdict(self)
