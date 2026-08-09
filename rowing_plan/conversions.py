"""Concept2 power/split conversions used consistently across the application."""
from __future__ import annotations

def watts_to_split_seconds(watts: float) -> float:
    if watts <= 0: raise ValueError("Watts must be positive.")
    return 500 * (2.8 / watts) ** (1 / 3)

def split_seconds_to_watts(split_seconds: float) -> float:
    if split_seconds <= 0: raise ValueError("Split seconds must be positive.")
    return 2.8 / ((split_seconds / 500) ** 3)

def two_k_seconds_to_watts(seconds: float) -> float:
    if seconds <= 0: raise ValueError("2k time must be positive.")
    return split_seconds_to_watts(seconds / 4)

def format_split(seconds: float | None) -> str:
    if seconds is None: return "—"
    mins, sec = divmod(seconds, 60)
    return f"{int(mins)}:{sec:04.1f}/500m"
