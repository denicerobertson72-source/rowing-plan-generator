# Taper and recovery transformation audit

Before Step 5, taper effects occur in three separate places: daily phase
labels change a normal band toward UT3, Step 2 reduces weekly intent volume by
day-weighted factors, and Step 4 chooses taper-compatible archetypes. Race
days become `RACE`; post-race days receive `race_recovery`.

There is no structured post-instantiation transformation record. Work and
recovery parameters, strength fatigue, race-rate retention, frequency-change
reasons, and mixed-week day-specific reductions are not represented together.

The narrow integration boundary is immediately after Step 4 creates a concrete
session and before weekly totals/feasibility are calculated. The selector,
weekly intent, recurring placement, race dates, locked sessions and intensity
providers remain unchanged.
